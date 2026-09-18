"""The chat page's geometry, measured by a real layout engine rather than read off the stylesheet.

Every other suite over this page executes its script in node, which has no layout engine at all: it
can prove a decision function returns the right number, never that the resulting box lands where it
should. These four defects were all invisible to that -- each one is a box in the wrong place.

Measured on the page before this change, driving Chromium at the sizes below:

  * the Activity scope <select> had `flex:1`, so it absorbed the whole row: 271px wide around a
    150px longest option at the default 340px panel, and 611px wide at a 680px panel -- 461px of
    dead space in a panel whose whole point is density.
  * the panel's drag clamp was the constant pair (260, 680), which knows nothing about the window.
    At 1000x700 the splitter could be dragged until `#main` was 60px wide and the composer input
    26px. Below 980px the panel is a fixed overlay, and a 680px width stored from a larger window
    put it over the header at a 700px one: the Activity button occupied 612-679px and the panel
    started at 20px, so the control that closes the panel was underneath it.
  * `.ctrl` was a block, so `#modeCtrl`'s button and its round "i" wrapped onto separate LINES; the
    control row measured 58px tall from 700px through 1280px against 32px for a single line. At
    1000x700 `#ctxBar`'s content was wider than its box and `overflow:hidden` cut the chat name off
    mid-glyph, with no ellipsis to show it had happened.
  * the model menu's height clamp ignored the 6px gap `bottom:calc(100% + 6px)` puts between the
    menu and its button. It never pushed the menu off screen -- the 8px viewport margin absorbed
    it -- but a clamped menu's top edge landed 2px from the window rather than the 8px the code
    says it keeps, so the margin was not a margin. This is a correction, not one of the three
    reported defects.

The viewport matrix deliberately includes sizes nobody designs for -- a phone-shaped window, both
sides of the 980px overlay breakpoint, and a 1440x300 letterbox -- because a real user drags a
window to whatever shape their screen leaves them.
"""

from __future__ import annotations

import json

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()

# Contract constants, mirrored from the page. A test that recomputed them from the page could not
# fail when the page changed them.
MAIN_MIN = 380
PANEL_MIN = 260
PANEL_MAX = 680
OVERLAY_PEEK = 56
OVERLAY_BREAKPOINT = 980
POPOVER_VIEWPORT_MARGIN = 8

# The sizes named in the UX brief, plus the sloppy ones real windows actually take.
VIEWPORTS = [
    (520, 620),     # narrower than the sidebar breakpoint
    (700, 500),     # small and short, panel overlays
    (900, 420),     # letterboxed: the menu's height clamp has almost nothing to work with
    (900, 620),
    (1280, 860),
    (1900, 1200),
    (360, 780),     # phone-shaped
    (979, 600),     # one pixel below the overlay breakpoint
    (981, 600),     # one pixel above it, where the panel is in flow and squeezes #main
    (1024, 768),
    (1440, 300),    # letterbox: three quarters of the window is header and footer
    (1600, 900),
]

# 60 models with names longer than anything OpenRouter actually ships, so the menu is measured
# against a list that overflows every axis at once.
LONG_MODEL_NAMES = [
    f"NVIDIA: Nemotron {i} Ultra 550B A55B Instruct Turbo Preview{' (free)' if i % 3 == 0 else ''}"
    for i in range(60)
]

# Populate the two surfaces the runtime fills in from the server, using the same shapes it uses.
SETUP = r"""
() => {
  openPanel();                 // the real entry point, re-clamp included -- not a class flipped on
  const s = document.getElementById('xpScope');
  s.innerHTML = '';
  for (const [v, t] of PANEL_SCOPES) {
    const o = document.createElement('option'); o.value = v; o.textContent = t; s.appendChild(o);
  }
  s.value = 'chat';
  view.projectId = 'p1';
  _serverProjects['p1'] = { id: 'p1', name: 'vool-local-product', root: '/Users/x/vool-local-product', emoji: '', color: '' };
  displayedChat = 'c1';
  _lastSessions = [{ session_id: 'c1', title: 'Refactor the activity ledger pipeline end to end', emoji: '🔥' }];
  renderContextBar();
  view.mode = 'bypass_permissions';
  reflectMode();
}
"""

# The real cloud-model path: connect a key and let renderCloudModels() build the rows and reposition.
LOAD_MODELS = r"""
async () => {
  setCloudConnected(true);
  await renderCloudModels();
  return document.querySelectorAll('#modelPop .pop-item.cloud-dyn').length;
}
"""

REPORT = r"""
() => {
  const R = Math.round;
  const el = (s) => document.querySelector(s);
  const box = (s) => { const e = el(s); if (!e) return null; const b = e.getBoundingClientRect();
    return { x: R(b.x), y: R(b.y), w: R(b.width), h: R(b.height),
             top: R(b.top), right: R(b.right), bottom: R(b.bottom), left: R(b.left) }; };
  const overlap = (a, b) => {
    if (!a || !b) return 0;
    const x = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    return (x > 0 && y > 0) ? x : 0;       // sharing an x range across two rows is not an overlap
  };

  // The natural width of the scope select's longest option, measured in the select's own font.
  const sel = document.getElementById('xpScope');
  const probe = document.createElement('span');
  probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;font:' + getComputedStyle(sel).font;
  probe.textContent = [...sel.options].map((o) => o.textContent).sort((a, b) => b.length - a.length)[0] || '';
  document.body.appendChild(probe);
  const scopeNatural = R(probe.getBoundingClientRect().width);
  probe.remove();

  // Can the header's Activity toggle actually be clicked, or is something on top of it?
  const btn = document.getElementById('panelBtn');
  const bb = btn.getBoundingClientRect();
  const hit = document.elementFromPoint(R(bb.left + bb.width / 2), R(bb.top + bb.height / 2));
  const composerHit = (() => {
    const f = el('#main footer .composer-row');
    if (!f) return null;
    const r = f.getBoundingClientRect();
    const h = document.elementFromPoint(R(r.left + 40), R(r.top + r.height / 2));
    return h ? (h.closest('#main footer') ? 'composer' : (h.closest('#xpanel') ? 'panel' : 'other')) : null;
  })();

  const chips = [...document.querySelectorAll('#ctxBar .ctx-chip')];
  const ctxBar = document.getElementById('ctxBar');
  const controls = { mode: box('#modeBtn'), info: box('#modeInfo'), ctx: box('#ctxBar'), model: box('#modelBtn') };
  const present = Object.entries(controls).filter(([, b]) => b && b.w > 0);
  const overlaps = [];
  for (let i = 0; i < present.length; i++) {
    for (let j = i + 1; j < present.length; j++) {
      const px = overlap(present[i][1], present[j][1]);
      if (px > 0.5) overlaps.push([present[i][0], present[j][0], R(px)]);
    }
  }

  const pop = document.getElementById('modelPop');
  const popOpen = pop.classList.contains('open');
  const pb = popOpen ? pop.getBoundingClientRect() : null;

  return {
    viewport: { w: window.innerWidth, h: window.innerHeight },
    overlayMode: getComputedStyle(document.getElementById('xpanel')).position === 'fixed',
    scope: { box: box('#xpScope'), row: box('.xp-scope-row'), natural: scopeNatural,
             optionCount: sel.options.length },
    panel: box('#xpanel'),
    main: box('#main'),
    header: box('#main header'),
    headerVar: getComputedStyle(document.documentElement).getPropertyValue('--app-header-h').trim(),
    panelBtn: box('#panelBtn'),
    panelBtnClickable: !!(hit && hit.closest && hit.closest('#panelBtn')),
    panelBtnHitId: hit ? (hit.id || hit.className || hit.tagName) : null,
    resizeHandle: box('#xpResize'),
    resizeHandleClickable: (() => {
      const h = document.getElementById('xpResize');
      const r = h.getBoundingClientRect();
      if (r.width < 1) return false;
      const t = document.elementFromPoint(R(r.left + r.width / 2), R(r.top + r.height / 2));
      return !!(t && (t === h || (t.closest && t.closest('#xpResize'))));
    })(),
    composerHit: composerHit,
    input: box('#input'),
    send: box('#send'),
    controlBar: box('.control-bar'),
    controls: controls,
    controlOverlaps: overlaps,
    ctx: {
      clipped: ctxBar.scrollWidth > Math.ceil(ctxBar.getBoundingClientRect().width) + 1,
      barTitle: ctxBar.title || '',
      chips: chips.map((c) => {
        const n = c.querySelector('.cc-name');
        return { w: R(c.getBoundingClientRect().width), title: c.title || '',
                 visible: getComputedStyle(c).display !== 'none',
                 ellipsized: !!n && n.scrollWidth > n.clientWidth + 1 };
      }),
    },
    modeBtnTitle: document.getElementById('modeBtn').title,
    modelBtnTitle: document.getElementById('modelBtn').title,
    modelLbl: document.getElementById('modelLbl').textContent,
    popover: popOpen ? { top: R(pb.top), bottom: R(pb.bottom), left: R(pb.left), right: R(pb.right),
                         h: R(pb.height), w: R(pb.width),
                         scrollable: pop.scrollHeight > pop.clientHeight + 1 } : null,
  };
}
"""


def _launch():
    # Availability decisions live in the gate-aware helper: under VOOL_GATE a missing browser
    # FAILS the authoritative lane, outside it the long-standing availability skip remains.
    return served_browser.launch_chromium()


def _route(route):
    request = route.request
    if request.resource_type == "document":
        route.fulfill(status=200, content_type="text/html", body=HTML)
        return
    if "/api/cloud/models" in request.url:
        models = [{"id": f"vendor/model-{i}", "name": name, "free": i % 3 == 0}
                  for i, name in enumerate(LONG_MODEL_NAMES)]
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"models": models, "provider": "openrouter",
                                       "label": "OpenRouter", "auto_free_model": "auto"}))
        return
    if "/api/settings/credentials" in request.url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"credentials": [{"name": "llm.cloud.openrouter"}]}))
        return
    route.fulfill(status=200, content_type="application/json", body="{}")


def _drag_panel_splitter_left(page) -> None:
    """Drag the vertical splitter as far left as a mouse can physically take it."""
    handle = page.query_selector("#xpResize")
    b = handle.bounding_box()
    if not b or b["width"] < 1:
        return
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
    page.mouse.down()
    page.mouse.move(1, b["y"] + b["height"] / 2, steps=10)
    page.mouse.up()


def _drag_sidebar_splitter_right(page) -> None:
    handle = page.query_selector("#sideResize")
    b = handle.bounding_box()
    if not b or b["width"] < 1:
        return
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
    page.mouse.down()
    page.mouse.move(page.viewport_size["width"] - 1, b["y"] + b["height"] / 2, steps=10)
    page.mouse.up()


@pytest.fixture(scope="module")
def matrix():
    """One browser, every viewport, four passes each: rested, dragged, oversized-store, menu open."""
    ctx, browser = _launch()
    try:
        page = browser.new_page()
        page.on("pageerror", lambda e: None)
        page.route("**/*", _route)
        out: dict[str, dict] = {}
        for w, h in VIEWPORTS:
            key = f"{w}x{h}"
            page.set_viewport_size({"width": w, "height": h})

            # 1. As the page opens, with the panel showing.
            page.goto("http://vool.test/chat")
            page.wait_for_timeout(160)
            page.evaluate(SETUP)
            page.wait_for_timeout(40)
            out[f"{key}:rested"] = page.evaluate(REPORT)

            # 2. Splitters dragged to their extremes -- panel as wide as it will go, sidebar too.
            _drag_panel_splitter_left(page)
            _drag_sidebar_splitter_right(page)
            page.wait_for_timeout(40)
            out[f"{key}:dragged"] = page.evaluate(REPORT)

            # 3. A width stored from a larger window, re-read on load at this one.
            page.evaluate("() => { localStorage.setItem('vool_panel_w','680');"
                          "        localStorage.setItem('vool_sidebar_w','520'); }")
            page.goto("http://vool.test/chat")
            page.wait_for_timeout(160)
            page.evaluate(SETUP)
            page.wait_for_timeout(40)
            out[f"{key}:stored-oversize"] = page.evaluate(REPORT)
            page.evaluate("() => { localStorage.removeItem('vool_panel_w');"
                          "        localStorage.removeItem('vool_sidebar_w'); }")

            # 4. The model menu open over a 60-model list of very long names. Below the overlay
            # breakpoint the open panel deliberately covers the composer, so there is no menu to
            # open until it is dismissed -- which is itself only possible because the toggle and
            # the panel's own close button are no longer buried.
            page.goto("http://vool.test/chat")
            page.wait_for_timeout(160)
            page.evaluate(SETUP)
            rows = page.evaluate(LOAD_MODELS)
            assert rows > 0, f"{key}: the cloud model list did not render"
            panel_open = not page.evaluate(
                "() => getComputedStyle(document.getElementById('xpanel')).position === 'fixed'")
            if not panel_open:
                page.click("#panelBtn")
            page.click("#modelBtn")
            page.wait_for_function("document.querySelectorAll('#modelPop .pop-item.cloud-dyn').length >= 30")
            out[f"{key}:menu"] = page.evaluate(REPORT)
            out[f"{key}:menu"]["cloudRows"] = page.locator("#modelPop .pop-item.cloud-dyn").count()
            out[f"{key}:menu"]["panelOpen"] = panel_open

            # 5. The same menu with the Activity panel closed, so the composer is full width and
            # the menu's anchor sits somewhere else entirely. Fresh load: clicking the button again
            # on the page above would toggle the menu shut rather than reopen it.
            page.goto("http://vool.test/chat")
            page.wait_for_timeout(160)
            page.evaluate(LOAD_MODELS)
            page.click("#modelBtn")
            page.wait_for_function("document.querySelectorAll('#modelPop .pop-item.cloud-dyn').length >= 30")
            out[f"{key}:menu-panel-closed"] = page.evaluate(REPORT)

        # A width chosen on a large window, then the window itself dragged smaller -- no reload and
        # no splitter involved. This is how a user reaches the defect without ever touching a
        # splitter twice, and it is the one path a load-time clamp alone does not cover.
        page.set_viewport_size({"width": 1900, "height": 1200})
        page.goto("http://vool.test/chat")
        page.wait_for_timeout(160)
        page.evaluate(SETUP)
        _drag_panel_splitter_left(page)
        page.wait_for_timeout(40)
        out["shrink:before"] = page.evaluate(REPORT)
        for w, h in ((1280, 860), (1000, 700), (900, 620), (700, 500), (520, 620)):
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(80)
            out[f"shrink:{w}x{h}"] = page.evaluate(REPORT)
        yield out
    finally:
        browser.close()
        ctx.stop()


MENU_PASSES = (":menu", ":menu-panel-closed")


def _cases(matrix, suffix):
    return {k: v for k, v in matrix.items() if k.endswith(":" + suffix)}


def _layout(matrix):
    """Every pass where the Activity panel is on screen and no transient menu is over the page."""
    return {k: v for k, v in matrix.items() if not k.endswith(MENU_PASSES)}


# --------------------------------------------------------------------------------------------
# A. The Activity scope dropdown fits its content instead of the panel
# --------------------------------------------------------------------------------------------


def test_the_scope_dropdown_is_sized_by_its_longest_option_not_by_the_row(matrix) -> None:
    """Before: 271px around a 150px option at a 340px panel; 611px around it at a 680px one."""
    for key, r in _layout(matrix).items():
        scope, row, natural = r["scope"]["box"], r["scope"]["row"], r["scope"]["natural"]
        assert r["scope"]["optionCount"] == 3, key
        room = row["w"] - 40                    # the row's label and gaps
        if natural + 40 <= room:
            # A <select>'s own frame -- the dropdown arrow and its padding -- is not dead space.
            assert scope["w"] - natural <= 56, (
                f"{key}: {scope['w'] - natural}px of dead space around a {natural}px option"
            )
            assert scope["w"] < row["w"], f"{key}: the select still spans the whole row"


def test_the_scope_dropdown_takes_the_full_row_only_when_the_panel_cannot_hold_its_option(matrix) -> None:
    for key, r in _layout(matrix).items():
        scope, row = r["scope"]["box"], r["scope"]["row"]
        assert scope["right"] <= row["right"] + 1, f"{key}: the select overflows its row"
        if scope["w"] >= row["w"] - 40:
            assert r["scope"]["natural"] + 40 > row["w"] - 40, (
                f"{key}: full width used with {row['w']}px of room for a "
                f"{r['scope']['natural']}px option"
            )


def test_the_scope_dropdown_stays_readable_at_every_panel_width(matrix) -> None:
    for key, r in _layout(matrix).items():
        assert r["scope"]["box"]["w"] >= 60, f"{key}: the scope control shrank out of use"
        assert r["scope"]["box"]["h"] >= 16, f"{key}: the scope control has no height"


# --------------------------------------------------------------------------------------------
# B. Panel resize boundaries
# --------------------------------------------------------------------------------------------


def test_the_activity_panel_never_covers_the_button_that_opens_it(matrix) -> None:
    """The overlay used to start at y=0 and swallow the header along with the chat."""
    for key, r in _layout(matrix).items():
        assert r["panelBtnClickable"], (
            f"{key}: the Activity toggle is under {r['panelBtnHitId']!r} "
            f"(button {r['panelBtn']}, panel {r['panel']})"
        )


def test_the_overlay_panel_starts_below_the_real_header(matrix) -> None:
    for key, r in _layout(matrix).items():
        if not r["overlayMode"]:
            continue
        assert r["panel"]["top"] >= r["header"]["bottom"] - 1, (
            f"{key}: panel top {r['panel']['top']} against header bottom {r['header']['bottom']}"
        )
        assert r["headerVar"] == f"{r['header']['h']}px", f"{key}: {r['headerVar']!r}"


def test_the_chat_column_never_collapses_below_a_usable_composer(matrix) -> None:
    """At 1000x700 the splitter could leave #main 60px wide and the composer input 26px."""
    for key, r in _layout(matrix).items():
        if r["overlayMode"]:
            continue          # an overlaid panel floats over #main; it does not squeeze it
        assert r["main"]["w"] >= MAIN_MIN - 1, f"{key}: #main is {r['main']['w']}px"
        assert r["input"]["w"] >= 200, f"{key}: the composer input is {r['input']['w']}px"
        assert r["send"]["w"] >= 40, f"{key}: the Send button is {r['send']['w']}px"


def test_the_panel_honours_its_own_floor_and_ceiling_at_every_viewport(matrix) -> None:
    for key, r in _layout(matrix).items():
        w = r["panel"]["w"]
        assert PANEL_MIN - 1 <= w <= PANEL_MAX + 1, f"{key}: panel width {w}"
        if r["overlayMode"]:
            assert w <= max(PANEL_MIN, r["viewport"]["w"] - OVERLAY_PEEK) + 1, (
                f"{key}: a {w}px overlay on a {r['viewport']['w']}px window leaves no chat in view"
            )


def test_a_width_stored_on_a_larger_window_is_re_clamped_on_load(matrix) -> None:
    """No drag involved: 680px was legal where it was chosen and is not legal here."""
    for key, r in _cases(matrix, "stored-oversize").items():
        assert r["panelBtnClickable"], f"{key}: stored width buried the Activity toggle"
        if not r["overlayMode"]:
            assert r["main"]["w"] >= MAIN_MIN - 1, f"{key}: #main is {r['main']['w']}px"
        vw = r["viewport"]["w"]
        if r["overlayMode"]:
            assert r["panel"]["w"] <= max(PANEL_MIN, vw - OVERLAY_PEEK) + 1, f"{key}: {r['panel']}"
        elif vw < PANEL_MAX + MAIN_MIN:
            assert r["panel"]["w"] < PANEL_MAX, f"{key}: 680px survived a {vw}px window"


def test_shrinking_the_window_re_applies_the_boundaries_with_no_drag_at_all(matrix) -> None:
    """680px was legal at 1900px wide. Dragging the window to 1000px does not make it legal."""
    before = matrix["shrink:before"]
    assert before["panel"]["w"] == PANEL_MAX, f"precondition: panel is {before['panel']['w']}px at 1900px"
    for key, r in matrix.items():
        if not key.startswith("shrink:") or key == "shrink:before":
            continue
        assert r["panelBtnClickable"], f"{key}: the Activity toggle went under the panel on resize"
        if not r["overlayMode"]:
            assert r["main"]["w"] >= MAIN_MIN - 1, f"{key}: #main is {r['main']['w']}px after the resize"
        assert r["panel"]["w"] <= panel_ceiling(r) + 1, f"{key}: panel is {r['panel']['w']}px"


def panel_ceiling(report) -> int:
    vw = report["viewport"]["w"]
    if report["overlayMode"]:
        return max(PANEL_MIN, min(PANEL_MAX, vw - OVERLAY_PEEK))
    return max(PANEL_MIN, min(PANEL_MAX, vw - report["main"]["w"] - 10))


def test_dragging_the_sidebar_out_cannot_crush_the_chat_either(matrix) -> None:
    """The sidebar has its own splitter and the same arithmetic applies to it."""
    for key, r in _cases(matrix, "dragged").items():
        if r["overlayMode"]:
            continue
        assert r["main"]["w"] >= MAIN_MIN - 1, f"{key}: #main is {r['main']['w']}px after both drags"


def test_the_panel_splitter_is_reachable_wherever_the_panel_is_shown(matrix) -> None:
    """In overlay mode the handle kept its place in flow order -- underneath the panel."""
    for key, r in _layout(matrix).items():
        assert r["resizeHandleClickable"], f"{key}: the splitter is not on top: {r['resizeHandle']}"


def test_the_composer_is_reachable_whenever_the_panel_is_not_overlaying_it(matrix) -> None:
    for key, r in _layout(matrix).items():
        if r["overlayMode"]:
            continue
        assert r["composerHit"] == "composer", f"{key}: the composer is behind {r['composerHit']}"


# --------------------------------------------------------------------------------------------
# C. The composer control row
# --------------------------------------------------------------------------------------------


def test_the_control_row_stays_on_one_line_at_every_supported_width(matrix) -> None:
    """58px of row height from 700px to 1280px was the mode button's "i" on its own line."""
    for key, r in _layout(matrix).items():
        bar, mode = r["controlBar"], r["controls"]["mode"]
        assert bar["h"] <= mode["h"] + 8, (
            f"{key}: control row is {bar['h']}px tall around a {mode['h']}px button"
        )


def test_the_round_mode_explainer_never_drops_onto_its_own_line(matrix) -> None:
    for key, r in _layout(matrix).items():
        mode, info = r["controls"]["mode"], r["controls"]["info"]
        assert info["top"] < mode["bottom"] - 1, f"{key}: mode {mode}, info {info}"


def test_no_two_composer_controls_ever_overlap(matrix) -> None:
    for key, r in _layout(matrix).items():
        assert r["controlOverlaps"] == [], f"{key}: {r['controlOverlaps']}"


def test_the_context_chips_ellipsize_rather_than_being_cut_off(matrix) -> None:
    """`overflow:hidden` on the bar sliced the chat name mid-glyph with nothing to show for it."""
    for key, r in _layout(matrix).items():
        assert not r["ctx"]["clipped"], f"{key}: #ctxBar clips its own content"


def test_every_control_that_can_be_shortened_keeps_its_full_text_in_a_tooltip(matrix) -> None:
    for key, r in _layout(matrix).items():
        assert "Refactor the activity ledger pipeline end to end" in r["ctx"]["barTitle"], key
        assert "vool-local-product" in r["ctx"]["barTitle"], key
        for chip in r["ctx"]["chips"]:
            if chip["visible"]:
                assert chip["title"], f"{key}: a visible chip carries no tooltip"
        assert r["modeBtnTitle"].startswith("Bypass permissions"), f"{key}: {r['modeBtnTitle']!r}"


def test_the_project_chip_is_what_gives_way_first_and_the_chat_name_survives(matrix) -> None:
    """Priority collapse: the muted project chip goes before any named chip is reduced to nothing."""
    for key, r in _layout(matrix).items():
        visible = [c for c in r["ctx"]["chips"] if c["visible"]]
        assert visible, f"{key}: every context chip vanished"
        assert visible[-1]["w"] >= 40, f"{key}: the chat chip shrank to {visible[-1]['w']}px"


def test_a_long_model_name_never_pushes_the_row_past_its_container(matrix) -> None:
    for key, r in _layout(matrix).items():
        model, bar = r["controls"]["model"], r["controlBar"]
        assert model["right"] <= bar["right"] + 1, f"{key}: model button overflows the row"
        assert r["controls"]["mode"]["left"] >= bar["left"] - 1, f"{key}: mode button overflows left"


# --------------------------------------------------------------------------------------------
# D. The model menu, with a long list of long names
# --------------------------------------------------------------------------------------------


def test_the_model_menu_stays_inside_the_window_with_sixty_long_names(matrix) -> None:
    for suffix in ("menu", "menu-panel-closed"):
        for key, r in _cases(matrix, suffix).items():
            pop, vp = r["popover"], r["viewport"]
            assert pop, f"{key}: the menu did not open"
            # The margin the positioning code names is POPOVER_VIEWPORT_MARGIN, and it has to be
            # the margin that actually survives -- a clamp that spends it on the anchor gap leaves
            # the menu flush against the window while reporting that it did not.
            assert pop["top"] >= POPOVER_VIEWPORT_MARGIN, (
                f"{key}: the menu's top is {pop['top']}px, inside the {POPOVER_VIEWPORT_MARGIN}px margin"
            )
            assert pop["bottom"] <= vp["h"], f"{key}: the menu runs {pop['bottom'] - vp['h']}px below"
            assert pop["left"] >= 0, f"{key}: the menu's left edge is at {pop['left']}px"
            assert pop["right"] <= vp["w"], f"{key}: the menu runs {pop['right'] - vp['w']}px past the right"


def test_the_model_menu_scrolls_rather_than_hiding_its_top(matrix) -> None:
    """The defect was 754px of menu in 244px of room with `overflow-y:visible`."""
    for suffix in ("menu", "menu-panel-closed"):
        for key, r in _cases(matrix, suffix).items():
            assert r["popover"]["scrollable"], f"{key}: a 60-model list that does not scroll"
            assert r["popover"]["h"] >= 120, f"{key}: the menu is {r['popover']['h']}px tall"


def test_the_model_menu_is_usable_on_a_letterboxed_window(matrix) -> None:
    for key in ("900x420:menu", "1440x300:menu"):
        r = matrix[key]
        assert r["popover"]["h"] >= 120, f"{key}: {r['popover']}"
        assert r["popover"]["top"] >= 0 and r["popover"]["bottom"] <= r["viewport"]["h"], key


def test_a_long_cloud_name_is_shortened_in_the_label_and_kept_in_the_tooltip(matrix) -> None:
    for key, r in _cases(matrix, "menu").items():
        assert r["cloudRows"] >= 30, f"{key}: only {r['cloudRows']} cloud rows rendered"
        assert len(r["modelLbl"]) <= 30, f"{key}: label {r['modelLbl']!r}"
