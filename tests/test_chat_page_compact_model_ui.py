"""The composer stays on one line and the model menu stays inside the window.

Measured on the running page before this change, with a 43-model OpenRouter list and the label
"NVIDIA: Nemotron 3 Ultra 550B A55B (free)" (41 characters):

  * 900x620 window -- the model menu rendered 754px tall with its top 256px ABOVE the viewport, so
    "VOOL Auto" and the whole Local section could only be reached by maximising the app. The menu
    had `overflow-y: visible`, so scrolling could not reach them either.
  * a 620px content width -- the control row went from 32px to 71px: Mode | Project/Chat | Model
    wrapped onto a second line because the model label was rendered in full.
  * 1900px window -- the spacer between the context chips and the model button was 1026px, 64% of
    the row, which is what made the controls look unrelated to one another.

The geometry below is injected rather than laid out (node has no layout engine), so these tests
exercise the real decision functions at real viewport sizes. The pixel results quoted above came
from driving the page in a browser at each size.
"""

from __future__ import annotations

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node, script

HTML = render_vool_chat_html()

# The viewport cases this page is expected to survive, smallest first.
SMALL = (900, 620)      # a small-ish native VOOL window
NORMAL = (1280, 860)
LARGE = (1900, 1200)
SHORT = (900, 420)      # deliberately cramped: the height clamp's worst case
NARROW = (520, 620)     # below the wrap breakpoint


def _cases() -> dict:
    return run_node(
        DOM
        + script()
        + """
// A menu anchored to a control sitting near the bottom of the window, exactly like the composer's.
function popoverCase(viewportW, viewportH, opts) {
  const o = opts || {};
  const pop = document.createElement('div');
  pop.classList.add('open');
  const btn = document.createElement('button');
  const anchorTop = o.anchorTop === undefined ? viewportH - 60 : o.anchorTop;
  const anchorBottom = anchorTop + 32;
  btn.getBoundingClientRect = () => ({ top: anchorTop, bottom: anchorBottom, left: o.anchorLeft || 0,
                                       right: (o.anchorLeft || 0) + 200, width: 200, height: 32 });
  // The menu reports the box it WOULD occupy once the height clamp is applied.
  pop.getBoundingClientRect = () => ({
    top: 0, bottom: 0, height: 0,
    left: o.boxLeft === undefined ? 40 : o.boxLeft,
    right: o.boxRight === undefined ? 400 : o.boxRight,
    width: (o.boxRight === undefined ? 400 : o.boxRight) - (o.boxLeft === undefined ? 40 : o.boxLeft),
  });
  document.documentElement.clientWidth = viewportW;
  document.documentElement.clientHeight = viewportH;
  // A page that has not been laid out reports zero on BOTH of these -- measured in a hidden pane,
  // where clientHeight, innerHeight and visualViewport were all 0. The harness's stub innerHeight
  // would otherwise stand in for a viewport that does not exist.
  const realInnerH = window.innerHeight, realInnerW = window.innerWidth;
  if (!viewportH) { window.innerHeight = 0; window.innerWidth = 0; }
  positionPopover(pop, btn);
  window.innerHeight = realInnerH; window.innerWidth = realInnerW;
  return {
    maxHeight: parseInt(pop.style.maxHeight || '0', 10),
    maxWidth: parseInt(pop.style.maxWidth || '0', 10),
    transform: pop.style.transform || '',
    below: pop.classList.contains('below'),
    roomAbove: anchorTop - 8,
  };
}

// The composer label, through the real reflectModel path.
function labelCase(name) {
  CLOUD_MODEL_LABELS['probe/model'] = name;
  modelValue = 'probe/model';
  view.stickyModel = null;
  reflectModel();
  return { rendered: document.getElementById('modelLbl').textContent,
           tooltip: document.getElementById('modelBtn').title };
}

const LONG = 'NVIDIA: Nemotron 3 Ultra 550B A55B (free)';
out({
  small: popoverCase(900, 620),
  normal: popoverCase(1280, 860),
  large: popoverCase(1900, 1200),
  short: popoverCase(900, 420),
  // No room above at all: the menu must flip below rather than clamp itself to nothing.
  topAnchored: popoverCase(1280, 860, { anchorTop: 10 }),
  // A viewport that has not been laid out must leave the CSS clamp in charge.
  degenerate: popoverCase(0, 0),
  overflowLeft: popoverCase(520, 620, { boxLeft: -174, boxRight: 256 }),
  overflowRight: popoverCase(520, 620, { boxLeft: 300, boxRight: 700 }),
  insideAlready: popoverCase(1280, 860, { boxLeft: 100, boxRight: 500 }),
  longLabel: labelCase(LONG),
  shortLabel: labelCase('VOOL Auto'),
  exactlyAtCap: labelCase('x'.repeat(30)),
  oneOverCap: labelCase('y'.repeat(31)),
  longLabelLength: LONG.length,
});
"""
    )


# ---------------------------------------------------------------- popover stays in the window


def test_the_menu_is_never_taller_than_the_room_above_it() -> None:
    """The clipping defect: 754px of menu in 244px of room, with no way to scroll to the top."""
    cases = _cases()
    assert cases["errors"] == []
    for name in ("small", "normal", "large", "short"):
        case = cases[name]
        assert case["maxHeight"] <= case["roomAbove"], (
            f"{name}: menu allowed {case['maxHeight']}px in {case['roomAbove']}px of room"
        )
        assert case["maxHeight"] > 0, f"{name}: menu clamped to nothing"


def test_a_cramped_window_still_gets_a_usable_menu() -> None:
    cases = _cases()
    assert cases["short"]["maxHeight"] >= 140, "a short window must still open a usable menu"


def test_the_menu_flips_below_when_there_is_no_room_above() -> None:
    cases = _cases()
    assert cases["topAnchored"]["below"] is True
    assert cases["small"]["below"] is False, "a footer control must keep opening upward"


def test_an_unlaid_out_viewport_leaves_the_css_clamp_in_charge() -> None:
    """A hidden tab reports a zero-size viewport; writing a computed 0px would hide the menu."""
    cases = _cases()
    assert cases["degenerate"]["maxHeight"] == 0, "no inline height should be written"
    assert cases["degenerate"]["below"] is False


def test_the_menu_is_pulled_back_inside_the_left_and_right_edges() -> None:
    """Measured at a 520px window: the menu's left edge sat at -174px."""
    cases = _cases()
    assert cases["overflowLeft"]["transform"] == "translateX(182px)", cases["overflowLeft"]
    assert cases["overflowRight"]["transform"].startswith("translateX(-"), cases["overflowRight"]
    assert cases["insideAlready"]["transform"] == "", "a menu already inside must not be nudged"


def test_the_menu_is_never_wider_than_the_window() -> None:
    cases = _cases()
    assert 0 < cases["overflowLeft"]["maxWidth"] <= 520


# ---------------------------------------------------------------- the label stays compact


def test_a_long_cloud_name_is_capped_but_still_recoverable() -> None:
    cases = _cases()
    assert cases["longLabelLength"] == 41, "precondition: the reported name is 41 characters"
    rendered = cases["longLabel"]["rendered"]
    assert len(rendered) <= 30, f"label rendered {len(rendered)} characters: {rendered!r}"
    assert rendered.endswith("…"), "a truncated label must show it was truncated"
    assert rendered.startswith("NVIDIA: Nemotron 3 Ultra 550"), rendered
    # The exact identity is not destroyed -- it moves to the tooltip.
    assert "NVIDIA: Nemotron 3 Ultra 550B A55B (free)" in cases["longLabel"]["tooltip"]


def test_a_short_name_is_left_exactly_as_it_is() -> None:
    cases = _cases()
    assert cases["shortLabel"]["rendered"] == "VOOL Auto"
    assert "…" not in cases["shortLabel"]["rendered"]


def test_the_cap_is_inclusive_of_the_ellipsis() -> None:
    """30 characters render whole; 31 render as 29 + the ellipsis, never 31."""
    cases = _cases()
    assert cases["exactlyAtCap"]["rendered"] == "x" * 30
    assert len(cases["oneOverCap"]["rendered"]) == 30
    assert cases["oneOverCap"]["rendered"] == "y" * 29 + "…"


# ---------------------------------------------------------------- layout contract in the stylesheet


def test_the_control_row_does_not_wrap_at_supported_widths() -> None:
    assert ".control-bar { display:flex; align-items:center; gap:8px; flex-wrap:nowrap;" in HTML
    # Wrapping is still allowed below the narrowest supported width, where one line cannot hold it.
    # The threshold is the FOOTER's width, not the window's: the sidebar and the Activity panel can
    # take 900px of a 1280px window, and a viewport-keyed breakpoint read "wide" while the composer
    # was in fact 340px across. The media query is the same threshold, for engines without
    # container queries, so the two rules can never disagree about where the row wraps.
    assert "footer {" in HTML and "container-type:inline-size; container-name:composer;" in HTML
    assert "@container composer (max-width: 310px) {" in HTML
    assert "@media (max-width: 310px) {" in HTML
    assert ".control-bar { flex-wrap:wrap; }" in HTML


def test_the_row_does_not_spread_apart_on_a_large_window() -> None:
    """The latest ux-pass1 authority keeps routing in the top bar, outside composer geometry."""
    header = HTML[HTML.index("<header>"):HTML.index("</header>")]
    footer = HTML[HTML.index("<footer>"):HTML.index("</footer>")]
    assert header.count('id="modelCtrl"') == 1
    assert 'id="modelCtrl"' not in footer
    assert '<span class="model-lane auto" id="modelLane">AUTO</span>' in header


def test_every_control_in_the_row_has_a_floor_it_cannot_shrink_through() -> None:
    """Composer controls retain their floors; top-bar routing has its own readable floor."""
    assert "#modeCtrl { flex:0 1 auto; min-width:116px; }" in HTML
    assert "#modelBtn { min-width:132px;" in HTML
    # `overflow:hidden` makes flex's automatic minimum resolve to zero rather than to min-content,
    # so the context bar has to state its floor rather than inherit one.
    assert ".ctx-bar { display:flex; align-items:center; gap:6px; flex:0 4 auto; min-width:56px; overflow:hidden; }" in HTML


def test_the_mode_button_and_its_explainer_share_one_flex_row() -> None:
    """`.ctrl` was a block, so the round "i" wrapped onto a line of its own."""
    assert ".ctrl { position:relative; display:inline-flex; align-items:center; gap:6px; min-width:0; }" in HTML


def test_the_menu_scrolls_internally_rather_than_growing_past_the_window() -> None:
    assert "max-height:min(72vh,560px); overflow-y:auto" in HTML
    # One scroller, not three: the inner lists no longer cap themselves.
    assert "#modelPop .cloud-list, #modelPop .local-list { max-height:none; overflow:visible; }" in HTML


def test_a_running_turn_keeps_its_status_on_one_line() -> None:
    assert ".tc-stage { color:var(--muted); font-size:12px; margin-top:2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }" in HTML
    assert "-webkit-line-clamp:2" not in HTML.split(".tc-stage")[1][:400]
    # The full string is still available on the element.
    assert "r.stage.title = stageText;" in HTML
