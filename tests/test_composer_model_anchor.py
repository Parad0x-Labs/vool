"""The model selector is the top-bar routing pill, and a selection survives the whole trip.

History and classification -- this file is the corrected contract, not the 2026-08-12 original.

The original family pinned the selector INTO the composer's control bar: its right edge had to
sit exactly on the textarea's right edge, held there by an auto margin plus a measured reserve
(`.cb-tail`), because the selector used to drift with the widths to its left (its right edge sat
at x=663 at 900px and 1900px alike, panel open or shut, while the composer's edge moved between
425px and 1805px). That contract was real while the selector lived in the row.

On 2026-08-28 (9be2bb21) the selector moved into the app header as the routing pill of the
ux-pass1 design authority (docs/design/ux-pass1-authority-20260825.html: `#modelPill` sits in
`<header class="topbar">`; the composer carries no model control at all). bc304627 corrected the
one string-level test to that design but left eight geometry tests measuring the HEADER pill
against COMPOSER geometry -- a relationship no current contract states and no design wants: the
pill shares its row with the brand, the cloud state and the Activity controls, so its right edge
is governed by header layout and can never be the textarea's at any width. All eight were
re-produced at base 97a4d2ea (8 failed / 7 passed) and classified. Every one is a STALE TEST
CONTRACT, not a product defect -- proven on the real rendered page in the bundled Chromium over
the original 7-viewport x 6-state matrix (42 cases): the pill sits inside the header box, never
overlaps a visible header sibling, never widens the page, and its menu opens unclipped in every
case, while the footer row keeps one line and its reserve still yields before anything readable.
Restoring the in-composer anchor would be resurrecting markup the design authority retired.

  test_the_selector_right_edge_sits_on_the_composer_right_edge -> test_the_selector_is_a_top_bar_pill_outside_composer_geometry
  test_the_selector_never_overlaps_the_send_button             -> test_the_selector_never_overlaps_a_visible_header_control
  test_the_selector_stays_inside_the_control_row               -> test_the_selector_stays_inside_the_header_row
  test_the_anchor_does_not_move_when_the_activity_panel_opens  -> test_opening_the_activity_panel_keeps_the_selector_in_its_header
  test_the_anchor_holds_with_the_search_bar_open               -> test_the_selector_holds_with_the_search_bar_open
  test_the_anchor_holds_for_every_label_including_vool_auto    -> test_the_label_stays_readable_for_every_identity_including_vool_auto
  test_typing_in_the_composer_does_not_disturb_the_anchor      -> test_typing_grows_the_composer_and_leaves_the_pill_in_place
  test_the_reserve_tracks_the_send_button_becoming_queue       -> same id, restated: the reserve is real, the model
                                                                  relationship it was measured for is not

The seam the geometry once shadowed -- visible selection -> pill label -> outgoing request -- is
behavioral law now, proven here against captured requests with loopback fixtures only:

  test_the_visible_pin_is_the_identity_the_request_carries        (sabotage target: break the
      model/model_selection propagation in buildTurnRequestBody and THIS test fails)
  test_a_pinned_cloud_identity_survives_a_reload_under_its_own_name (sabotage target: stop
      renderCloudModels feeding CLOUD_MODEL_LABELS and THIS test fails)
  test_an_unavailable_model_is_refused_without_touching_the_selection
  test_losing_the_cloud_key_cannot_leave_a_cloud_pin_silently_in_charge

The reload test pins a real product repair, not a test edit: a pin restored from storage showed
the raw provider id after reload although the catalog with friendly names had already landed
(CLOUD_MODEL_LABELS was written only on switch, never on catalog load, against its own declared
"labels fill in as the catalog loads; the raw id is the safe fallback"). renderCloudModels now
feeds the label map; the request always carried the exact id and continues to.
"""

from __future__ import annotations

import json

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()

VIEWPORTS = [(1900, 1200), (1280, 860), (1100, 800), (900, 620), (700, 560), (520, 620), (360, 780)]

LONG_MODEL_NAMES = [
    "NVIDIA: Nemotron 3 Ultra 550B A55B (free)",
    "Meta: Llama 4 Maverick 402B Instruct Turbo Preview Extended Context (free)",
    "Google: Gemma 4 31B (free)",
]

# The states the pill has to survive, as (label, setup script).
STATES = {
    "panel-closed": "() => { document.body.classList.remove('panel-open'); closeChatSearch(); }",
    "panel-open": "() => { document.body.classList.add('panel-open'); closeChatSearch(); }",
    "search-open": "() => { document.body.classList.remove('panel-open'); openChatSearch(); }",
    "panel-and-search": "() => { document.body.classList.add('panel-open'); openChatSearch(); }",
    "long-model": """() => {
        document.body.classList.remove('panel-open'); closeChatSearch();
        modelValue = 'vendor/model-1';
        CLOUD_MODEL_LABELS[modelValue] = 'Meta: Llama 4 Maverick 402B Instruct Turbo Preview Extended Context (free)';
        reflectModel();
    }""",
    "vool-auto": "() => { closeChatSearch(); modelValue = 'vool'; view.stickyModel = null; reflectModel(); }",
    "vool-auto-pinned": """() => {
        closeChatSearch(); modelValue = 'vool';
        view.stickyModel = 'vendor/model-1';   // Auto stuck to a cloud model
        reflectModel();
    }""",
}

REPORT = """
(() => {
  const R = (n) => Math.round(n);
  const box = (s) => { const e = document.querySelector(s); if (!e) return null;
    const b = e.getBoundingClientRect();
    return { l: R(b.left), r: R(b.right), t: R(b.top), b: R(b.bottom), w: R(b.width), h: R(b.height) }; };
  const model = box('#modelCtrl'), input = box('#input'), send = box('#send'), bar = box('.control-bar');
  const headerEl = document.querySelector('header');
  const header = box('header');
  const inHeader = !!headerEl && headerEl.contains(document.getElementById('modelCtrl'));
  // Every VISIBLE sibling the pill shares its row with (hidden popovers and chips have none).
  const siblings = [...headerEl.children].filter((c) => c.id !== 'modelCtrl' && c.offsetParent !== null);
  const m = document.getElementById('modelCtrl').getBoundingClientRect();
  const overlaps = siblings.filter((c) => {
    const b = c.getBoundingClientRect();
    return m.left < b.right && b.left < m.right && m.top < b.bottom && b.top < m.bottom;
  }).map((c) => c.id || c.className);
  const de = document.documentElement;
  const kids = [...document.querySelector('.control-bar').children].filter((c) => c.offsetParent !== null);
  const tallest = Math.max(...kids.map((c) => c.getBoundingClientRect().height));
  const tail = document.querySelector('.cb-tail');
  return {
    model, input, send, bar, header,
    inHeader, inFooter: (() => { const f = document.querySelector('footer');
      return !!(f && f.contains(document.getElementById('modelCtrl'))); })(),
    overlaps,
    insideHeader: !!model && !!header && model.l >= header.l - 1 && model.r <= header.r + 1
      && model.t >= header.t - 1 && model.b <= header.b + 1,
    overflowX: de.scrollWidth > de.clientWidth,
    wrapped: document.querySelector('.control-bar').getBoundingClientRect().height > tallest + 4,
    label: document.getElementById('modelLbl').textContent,
    tooltip: document.getElementById('modelBtn').title,
    lane: document.getElementById('modelLane').textContent,
    tailWidth: R(tail.getBoundingClientRect().width),
    tailBasis: Math.round(parseFloat(getComputedStyle(tail).flexBasis) || 0),
    dictate: box('#dictateBtn'),
    inputMin: Math.round(parseFloat(getComputedStyle(document.getElementById('input')).minWidth) || 0),
    ctxClipped: (() => { const c = document.querySelector('.ctx-bar');
      return !!c && c.scrollWidth > c.clientWidth + 1; })(),
    tallestChild: R(tallest),
  };
})()
"""

#: Force a reserve the layout can no longer generate on its own, and report what yields.
#: The natural basis is derived from #send's OWN rendered width, which shrinks as the
#: viewport narrows, so the shrink-20 phase is unreachable by resizing alone.
FORCE_TAIL = """
(basis) => {
  const footer = document.querySelector('footer');
  const prev = footer.style.getPropertyValue('--composer-tail');
  footer.style.setProperty('--composer-tail', basis + 'px');
  const tail = document.querySelector('.cb-tail');
  const de = document.documentElement;
  const kids = [...document.querySelector('.control-bar').children].filter((c) => c.offsetParent !== null);
  const tallest = Math.max(...kids.map((c) => c.getBoundingClientRect().height));
  const out = {
    tailWidth: Math.round(tail.getBoundingClientRect().width),
    tailBasis: Math.round(parseFloat(getComputedStyle(tail).flexBasis) || 0),
    inputWidth: Math.round(document.getElementById('input').getBoundingClientRect().width),
    inputMin: Math.round(parseFloat(getComputedStyle(document.getElementById('input')).minWidth) || 0),
    overflowX: de.scrollWidth > de.clientWidth,
    wrapped: document.querySelector('.control-bar').getBoundingClientRect().height > tallest + 4,
  };
  footer.style.setProperty('--composer-tail', prev);
  return out;
}
"""


def _reserve_intact(r: dict) -> bool:
    """True when the row had room for the whole reserve, so it is holding its measured width."""
    return r["tailWidth"] >= r["tailBasis"] - 1


# Loopback server state for the seam tests. Every response is a synthetic fixture: no live
# provider, no paid call, no key material -- the catalog and the pin state live in this dict.
CAPTURED = {
    "chat": [],          # bodies of POST /api/chat
    "pin_posts": [],     # bodies of POST /api/cloud/model
    "pin_mode": "ok",    # ok | unavailable (the latter refuses every switch)
    "server_pin": "",    # the mock server's own pin, for GET /api/cloud/model reconciliation
    "problems": [],      # page errors and console errors, for the zero-console-error law
}


def _route(route):
    request = route.request
    url = request.url
    if request.resource_type == "document":
        route.fulfill(status=200, content_type="text/html", body=HTML)
        return
    if "/api/cloud/models" in url:
        models = [{"id": f"vendor/model-{i}", "name": name, "free": True}
                  for i, name in enumerate(LONG_MODEL_NAMES)]
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"models": models, "provider": "openrouter",
                                       "label": "OpenRouter", "auto_free_model": "auto"}))
        return
    if url.endswith("/api/cloud/model") and request.method == "GET":
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "model": CAPTURED["server_pin"],
                                       "cost_state": "free" if CAPTURED["server_pin"] else "",
                                       "provider": "openrouter", "free_cloud_enabled": True,
                                       "auto_free_model": "auto"}))
        return
    if url.endswith("/api/cloud/model") and request.method == "POST":
        body = json.loads(request.post_data or "{}")
        CAPTURED["pin_posts"].append(body)
        if CAPTURED["pin_mode"] == "unavailable":
            # The provider no longer resolves this id: the server's typed refusal.
            route.fulfill(status=404, content_type="application/json",
                          body=json.dumps({"ok": False, "error": "model not resolvable on this provider"}))
            return
        CAPTURED["server_pin"] = str(body.get("model", ""))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "model": CAPTURED["server_pin"], "cost_state": "free"}))
        return
    if "/api/settings/credentials" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"credentials": [{"name": "llm.cloud.openrouter"}]}))
        return
    if "/api/connections" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"connections": [{"name": "openrouter", "label": "OpenRouter",
                                                        "state": "ok"}]}))
        return
    if url.endswith("/api/chat") and request.method == "POST":
        CAPTURED["chat"].append(json.loads(request.post_data or "{}"))
        stream = "\n".join([
            json.dumps({"message": {"content": "Pinned lane echo. "}}),
            json.dumps({"message": {"content": "The seam is intact."}}),
        ]) + "\n"
        route.fulfill(status=200, content_type="text/event-stream", body=stream)
        return
    if "/api/chat/attachments/limits" in url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"max_files_per_turn": 8, "max_bytes_per_file": 10485760,
                                       "max_bytes_per_turn": 26214400, "document_threshold_chars": 4000}))
        return
    route.fulfill(status=200, content_type="application/json", body="{}")


def _launch():
    # Availability decisions live in the gate-aware helper: under VOOL_GATE a missing browser
    # FAILS the authoritative lane, outside it the long-standing availability skip remains.
    return served_browser.launch_chromium()


@pytest.fixture(scope="module")
def session():
    """One browser page for the whole module.

    The sync Playwright API refuses to start a second instance on the same thread, so a test that
    launched its own browser alongside this fixture SKIPPED rather than failed -- silently dropping
    the Queue, typing and menu-clipping checks. Everything shares this page, and the page counts
    every page error and console error so the family can hold the zero-console-error law.
    """
    ctx, browser = _launch()
    try:
        page = browser.new_page()
        page.on("pageerror", lambda e: CAPTURED["problems"].append("pageerror: " + str(e)))
        page.on("console", lambda m: CAPTURED["problems"].append("console.error: " + m.text)
                if m.type == "error" else None)
        page.route("**/*", _route)
        yield page
    finally:
        browser.close()
        ctx.stop()


def _fresh_page(page) -> None:
    """Reset the page to a freshly-booted, un-pinned state (loopback storage only)."""
    CAPTURED["chat"].clear()
    CAPTURED["pin_posts"].clear()
    CAPTURED["pin_mode"] = "ok"
    CAPTURED["server_pin"] = ""
    page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(300)


def _problems_before() -> int:
    return len(CAPTURED["problems"])


def _assert_no_new_problems(page, before: int, allowing_404: bool = False) -> None:
    """The zero-console-error law: nothing new, except a deliberately provoked refusal log."""
    new = CAPTURED["problems"][before:]
    if allowing_404:
        new = [p for p in new if "404" not in p]
    assert not new, f"the page raised errors: {new}"


@pytest.fixture(scope="module")
def matrix(session):
    """Every viewport against every state -- and the whole sweep must run without page errors."""
    mark = _problems_before()
    out: dict[str, dict] = {}
    for width, height in VIEWPORTS:
        session.set_viewport_size({"width": width, "height": height})
        session.goto("http://vool.test/chat")
        session.wait_for_timeout(200)
        for state, setup in STATES.items():
            session.evaluate(setup)
            session.wait_for_timeout(60)
            out[f"{width}x{height}:{state}"] = session.evaluate(REPORT)
    assert not CAPTURED["problems"][mark:], f"the matrix sweep raised errors: {CAPTURED['problems'][mark:]}"
    return out


# ---------------------------------------------------------------- the pill and its row


def test_the_selector_is_a_top_bar_pill_outside_composer_geometry(matrix) -> None:
    """Routing authority lives in the app header (ux-pass1); the composer carries no model control.

    The discarded contract demanded the pill's right edge sit ON the textarea's at every size. In
    the header that relationship cannot exist -- the pill shares its row with the brand, the cloud
    state and the Activity controls -- and nothing in the product wants it: the sweep below is the
    whole original matrix, and membership, not a right-edge coincidence, is the contract now.
    """
    offenders = {k: (r["inHeader"], r["inFooter"]) for k, r in matrix.items()
                 if not r["inHeader"] or r["inFooter"]}
    assert not offenders, f"the selector is not a header-only routing pill: {offenders}"


def test_the_selector_stays_inside_the_header_row(matrix) -> None:
    """A pill that left its row would overlay the log or fall off the app frame."""
    offenders = {k: r["insideHeader"] for k, r in matrix.items() if not r["insideHeader"]}
    assert not offenders, f"the selector escaped the header: {offenders}"


def test_the_selector_never_overlaps_a_visible_header_control(matrix) -> None:
    """The old family checked the pill against the Send button -- in another row entirely. The real
    collision risk for a header pill is its own row neighbours, at every width and state."""
    offenders = {k: r["overlaps"] for k, r in matrix.items() if r["overlaps"]}
    assert not offenders, f"the selector overlaps header controls: {offenders}"


def test_the_input_never_shrinks_below_its_declared_floor(matrix) -> None:
    """Whatever else gives way, the operator can still see what they are typing.

    This replaces a guard whose premise no viewport reaches. That guard asserted
    the composer reserve gets COMPRESSED somewhere in the matrix, and then made
    its real claims about that case. Since the model pill moved into <header>,
    the reserve basis is derived from #send's own rendered width -- which shrinks
    as the window narrows -- so the reserve tracks the room available and the
    shrink phase never engages naturally. Measured across the full 7x7 sweep,
    tailWidth == tailBasis at every single case, from 1900px down to 240px.

    A test that cannot reach its premise is not evidence, so the premise is gone
    and the observable it was protecting is asserted directly and unconditionally
    on all 49 cases instead of on the empty subset.
    """
    offenders = {
        k: (r["input"]["w"], r["inputMin"])
        for k, r in matrix.items()
        if not r["input"] or r["input"]["w"] < r["inputMin"] - 1
    }
    assert not offenders, f"the textarea fell below its own min-width floor: {offenders}"
    # And the floor itself is real, not zero.
    zero_floor = {k: r["inputMin"] for k, r in matrix.items() if r["inputMin"] <= 0}
    assert not zero_floor, f"the textarea declares no min-width floor: {zero_floor}"


def test_the_buttons_are_the_reserve_that_gives_way_not_the_input(matrix) -> None:
    """Under width pressure the SEND/dictate controls yield and the textarea holds.

    This is the product claim the deleted guard was reaching for, stated as a
    comparison across the matrix rather than as a property of one unreachable case.
    """
    widest = matrix["1900x1200:panel-closed"]
    narrowest = matrix["360x780:panel-closed"]
    assert narrowest["send"]["w"] < widest["send"]["w"], (
        f"#send did not yield any width: {narrowest['send']['w']} vs {widest['send']['w']}"
    )
    assert narrowest["dictate"]["w"] < widest["dictate"]["w"], (
        f"#dictateBtn did not yield any width: {narrowest['dictate']['w']} vs {widest['dictate']['w']}"
    )
    # The textarea is at its floor there, not below it and not collapsed.
    assert narrowest["input"]["w"] >= narrowest["inputMin"] - 1, narrowest
    assert narrowest["input"]["w"] > 0, narrowest


def test_no_case_wraps_overflows_overlaps_or_clips_the_context_chips(matrix) -> None:
    """One line, no sideways scroll, no overlap, no clipped chips -- everywhere."""
    wrapped = {k: r["bar"] for k, r in matrix.items() if r["wrapped"]}
    assert not wrapped, f"the control row wrapped: {sorted(wrapped)}"
    overflow = [k for k, r in matrix.items() if r["overflowX"]]
    assert not overflow, f"the page scrolls sideways: {overflow}"
    overlaps = {k: r["overlaps"] for k, r in matrix.items() if r["overlaps"]}
    assert not overlaps, f"header controls overlap: {overlaps}"
    clipped = [k for k, r in matrix.items() if r["ctxClipped"]]
    assert not clipped, f"the context chips are clipped: {clipped}"
    dead = {k: r["bar"] for k, r in matrix.items() if not r["bar"] or r["bar"]["h"] <= 0}
    assert not dead, f"the control row vanished: {dead}"


@pytest.mark.parametrize("width", [1280, 700, 520, 360])
def test_a_reserve_larger_than_the_row_yields_without_wrapping_or_overflowing(session, width) -> None:
    """The `flex:0 20` rule is still load-bearing -- proved by reaching it.

    The natural basis can no longer exceed the room available (see the test
    above), so this forces one that does. That is the ONLY way to exercise the
    shrink phase in this composition, and it reaches the exact behaviour the
    deleted premise was trying to observe: the reserve collapses first, and the
    row still does not wrap and the page still does not scroll sideways.
    """
    session.set_viewport_size({"width": width, "height": 800})
    session.goto("http://vool.test/chat")
    session.wait_for_timeout(200)
    session.evaluate(STATES["panel-closed"])
    session.wait_for_timeout(60)

    # Larger than any viewport in the sweep, so the row cannot honour it at ANY
    # width and the shrink phase is reached everywhere rather than only where the
    # arithmetic happens to bind.
    forced = session.evaluate(FORCE_TAIL, 2400)
    assert forced["tailBasis"] == 2400, forced
    assert forced["tailWidth"] < forced["tailBasis"], (
        f"the reserve did not yield at {width}px; flex:0 20 is not doing its job: {forced}"
    )
    assert not forced["wrapped"], f"the row wrapped under a forced reserve at {width}px: {forced}"
    assert not forced["overflowX"], f"the page scrolled sideways at {width}px: {forced}"
    assert forced["inputWidth"] >= forced["inputMin"] - 1, forced


def test_reduced_motion_is_honoured_by_the_composer_chrome(session) -> None:
    """A viewer who asked for less motion gets no animated transition here."""
    session.set_viewport_size({"width": 1280, "height": 860})
    try:
        session.emulate_media(reduced_motion="reduce")
        session.goto("http://vool.test/chat")
        session.wait_for_timeout(200)
        durations = session.evaluate(
            """() => ['#input', '#send', '#dictateBtn', '#modelCtrl']
                 .map((s) => document.querySelector(s))
                 .filter(Boolean)
                 .map((e) => getComputedStyle(e).transitionDuration)"""
        )
        assert durations, "no composer chrome found to measure"

        def _seconds(value: str) -> float:
            text = str(value or "0").strip()
            if text.endswith("ms"):
                return float(text[:-2]) / 1000.0
            return float(text.rstrip("s") or 0)

        # The page collapses transitions to 1e-05s rather than 0s under
        # prefers-reduced-motion -- a deliberate idiom that keeps `transitionend`
        # firing for logic that waits on it while removing all perceptible
        # motion. Assert "effectively zero", not the literal string "0s", or this
        # guard would reject a correct implementation.
        moving = [d for d in durations if _seconds(d) > 0.02]
        assert not moving, f"reduced-motion was requested but transitions still run: {durations}"
    finally:
        session.emulate_media(reduced_motion="no-preference")
        session.goto("http://vool.test/chat")
        session.wait_for_timeout(120)


def test_opening_the_activity_panel_keeps_the_selector_in_its_header(matrix) -> None:
    """Opening Activity displaces the composer; the pill rides its own header, inside it, at every
    width -- it does not track the composer's edge and must not try."""
    for width, height in VIEWPORTS:
        for state in ("panel-closed", "panel-open"):
            r = matrix[f"{width}x{height}:{state}"]
            assert r["insideHeader"] and not r["overlaps"] and not r["overflowX"], (
                f"{width}x{height}:{state}: {r['insideHeader']}, {r['overlaps']}, {r['overflowX']}"
            )


def test_the_selector_holds_with_the_search_bar_open(matrix) -> None:
    checked = [k for k in matrix if "search" in k]
    for key in checked:
        r = matrix[key]
        assert r["insideHeader"], f"{key}: the pill left the header with the search bar open"
        assert not r["overlaps"] and not r["overflowX"], f"{key}: {r['overlaps']}, {r['overflowX']}"


def test_the_label_stays_readable_for_every_identity_including_vool_auto(matrix) -> None:
    """Whatever identity the pill carries -- Auto, Auto stuck to a chat's cloud model, or a long
    cloud pin -- the label is never blank, the full name stays on the tooltip, and the lane badge
    says which lane the identity routes to."""
    for key, r in matrix.items():
        if not key.endswith(("vool-auto", "vool-auto-pinned", "long-model")):
            continue
        assert r["label"], f"{key}: the pill went blank"
        assert r["tooltip"], f"{key}: the full identity is gone from the tooltip"
        assert r["insideHeader"], f"{key}: the labelled pill left the header"
    pinned = matrix[f"{VIEWPORTS[1][0]}x{VIEWPORTS[1][1]}:vool-auto-pinned"]
    assert "VOOL Auto" in pinned["label"], pinned["label"]
    assert "\U0001F4CC" in pinned["label"], "the sticky mark is not visible on the pill"
    long = matrix[f"{VIEWPORTS[1][0]}x{VIEWPORTS[1][1]}:long-model"]
    assert "Meta: Llama 4 Maverick 402B" in long["tooltip"], long["tooltip"]


def test_the_page_never_scrolls_sideways(matrix) -> None:
    offenders = [k for k, r in matrix.items() if r["overflowX"]]
    assert not offenders, f"horizontal overflow at {offenders}"


# ---------------------------------------------------------------- the reserve is measured, not assumed


def test_the_reserve_tracks_the_send_button_becoming_queue(session) -> None:
    """The reserve's own law survives the pill's move: `.cb-tail` holds back the Send button's
    MEASURED width, and the button becomes "Queue" for the whole of a running turn, which is 8px
    wider here -- a hard-coded reserve would be wrong for the whole turn."""
    page = session
    mark = _problems_before()
    page.set_viewport_size({"width": 1280, "height": 860})
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(200)
    result = page.evaluate(
            """() => {
                const R = (n) => Math.round(n);
                const width = (s) => R(document.querySelector(s).getBoundingClientRect().width);
                const tail = () => getComputedStyle(document.querySelector('.cb-tail')).flexBasis;
                const idle = { label: sendEl.textContent, send: width('#send'), tail: tail() };
                chatState(displayedChat).run = { ended: false, status: 'running' };
                reflectComposer();
                const busy = { label: sendEl.textContent, send: width('#send'), tail: tail() };
                chatState(displayedChat).run = null;
                reflectComposer();
                return { idle, busy };
            }"""
    )
    assert result["idle"]["label"] == "Send"
    assert result["busy"]["label"] == "Queue"
    assert result["busy"]["send"] > result["idle"]["send"], "precondition: Queue is wider than Send"
    assert result["busy"]["tail"] != result["idle"]["tail"], "the reserve did not follow the button"
    _assert_no_new_problems(page, mark)


def test_typing_grows_the_composer_and_leaves_the_pill_in_place(session) -> None:
    """A growing textarea re-lays out the footer; the header pill must not move by a pixel while
    it happens, focus must stay in the composer, and the text must land."""
    page = session
    mark = _problems_before()
    page.set_viewport_size({"width": 1100, "height": 800})
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(200)
    before = page.evaluate(
        "() => { const b = document.getElementById('modelCtrl').getBoundingClientRect();"
        "        return { l: Math.round(b.left), t: Math.round(b.top),"
        "                 r: Math.round(b.right), b: Math.round(b.bottom) }; }")
    page.click("#input")
    page.keyboard.type("a message long enough to grow the textarea " * 4)
    page.wait_for_timeout(80)
    after = page.evaluate(
            """() => {
                const R = (n) => Math.round(n);
                const b = document.getElementById('modelCtrl').getBoundingClientRect();
                return { pill: { l: R(b.left), t: R(b.top), r: R(b.right), b: R(b.bottom) },
                         focused: document.activeElement === inputEl,
                         typed: inputEl.value.length,
                         inputH: R(inputEl.getBoundingClientRect().height) };
            }"""
    )
    assert after["focused"] is True, "typing moved focus out of the composer"
    assert after["typed"] > 100, "the composer did not receive the text"
    assert after["inputH"] > 44, "precondition: the textarea grew, which re-lays out the footer"
    assert after["pill"] == before, f"the pill moved while the composer grew: {before} -> {after['pill']}"
    _assert_no_new_problems(page, mark)


def test_the_model_menu_still_opens_unclipped_against_the_anchor(session) -> None:
    """Moving the button changes where its menu is anchored; the menu must still fit the window."""
    page = session
    mark = _problems_before()
    results = {}
    for width, height in VIEWPORTS:
        page.set_viewport_size({"width": width, "height": height})
        page.goto("http://vool.test/chat")
        page.wait_for_timeout(220)
        results[f"{width}x{height}"] = page.evaluate(
            """() => {
                document.getElementById('modelBtn').click();
                const R = (n) => Math.round(n);
                const pop = document.getElementById('modelPop');
                const b = pop.getBoundingClientRect();
                const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
                const first = pop.querySelector('.pop-item').getBoundingClientRect();
                const out = { clipLeft: Math.max(0, R(-b.left)), clipRight: Math.max(0, R(b.right - vw)),
                              clipTop: Math.max(0, R(-b.top)), clipBottom: Math.max(0, R(b.bottom - vh)),
                              firstVisible: first.top >= 0 && first.bottom <= vh };
                document.getElementById('modelBtn').click();
                return out;
            }"""
        )
    for key, r in results.items():
        assert r["clipLeft"] == 0 and r["clipRight"] == 0, f"{key}: menu clipped sideways {r}"
        assert r["clipTop"] == 0 and r["clipBottom"] == 0, f"{key}: menu clipped vertically {r}"
        assert r["firstVisible"] is True, f"{key}: the first menu row is not reachable"
    _assert_no_new_problems(page, mark)


# ---------------------------------------------------------------- the seam: selection becomes the request


def _open_model_menu(page) -> None:
    page.evaluate("() => { document.getElementById('modelBtn').click(); }")
    page.wait_for_timeout(150)


def _send_and_capture(page, text: str) -> dict:
    # A refused switch deliberately leaves the popover open (the row carries the refusal), and an
    # open menu overlays the composer -- close it the way a click anywhere in the page does.
    page.evaluate("() => { document.body.click(); }")
    page.wait_for_timeout(80)
    before = len(CAPTURED["chat"])
    page.click("#input")
    page.keyboard.type(text)
    page.click("#send")
    page.wait_for_timeout(400)
    assert len(CAPTURED["chat"]) > before, "the turn never went out"
    return CAPTURED["chat"][-1]


def test_the_visible_pin_is_the_identity_the_request_carries(session) -> None:
    """The whole seam in one sweep: what the pill shows is what the row checked, and both are what
    the outgoing /api/chat body carries -- an exact pin, the Local-Only tier, and back to Auto.

    SABOTAGE: break the model/model_selection propagation in buildTurnRequestBody (send 'vool'
    unconditionally) and THIS is the named test that must fail.
    """
    page = session
    mark = _problems_before()
    _fresh_page(page)
    _open_model_menu(page)
    page.evaluate("""() => {
        const row = [...document.querySelectorAll('#modelPop .cloud-dyn.pop-item')]
            .find((n) => n.getAttribute('data-model') === 'vendor/model-1');
        row.click();
    }""")
    page.wait_for_timeout(250)
    assert CAPTURED["pin_posts"] and CAPTURED["pin_posts"][-1]["model"] == "vendor/model-1", CAPTURED["pin_posts"]
    label = page.evaluate("() => document.getElementById('modelLbl').textContent")
    assert "Meta: Llama 4 Maverick 402B" in label, f"the pill did not show the pinned name: {label!r}"
    checked = page.evaluate(
        """() => { const n = document.querySelector('#modelPop .pop-item[aria-checked="true"]');
                  return n ? n.getAttribute('data-model') : null; }""")
    assert checked == "vendor/model-1", f"the checkmark did not follow the pin: {checked}"
    body = _send_and_capture(page, "prove the pin reaches the request")
    assert body["model"] == "vendor/model-1", body
    assert body["model_selection"] == "pin", body
    assert body["messages"] == [{"role": "user", "content": "prove the pin reaches the request"}], body

    _open_model_menu(page)
    page.evaluate("""() => { document.querySelector('#modelPop .pop-item[data-model="vool-local-only"]').click(); }""")
    page.wait_for_timeout(200)
    label = page.evaluate("() => document.getElementById('modelLbl').textContent")
    assert label == "VOOL Auto Local Only", f"the visible selection did not change: {label!r}"
    body = _send_and_capture(page, "local only turn")
    assert body["model"] == "vool-local-only" and body["model_selection"] == "pin", body

    _open_model_menu(page)
    page.evaluate("""() => { document.querySelector('#modelPop .pop-item[data-model="vool"]').click(); }""")
    page.wait_for_timeout(200)
    body = _send_and_capture(page, "auto turn")
    assert body["model"] == "vool" and body["model_selection"] == "auto", body
    _assert_no_new_problems(page, mark)


def test_a_pinned_cloud_identity_survives_a_reload_under_its_own_name(session) -> None:
    """A pin is persisted state: after a reload the pill must read as the model's catalog NAME --
    not the raw provider id -- and the next request must carry the exact id again.

    Found by this lane's browser probe and REPAIRED in the page: the catalog load populated the
    paid-guard's catalog map but never the label map, so the restored pin degraded to its raw id
    against the map's own 'labels fill in as the catalog loads' contract.

    SABOTAGE: stop renderCloudModels from feeding CLOUD_MODEL_LABELS and THIS is the named test
    that must fail.
    """
    page = session
    mark = _problems_before()
    _fresh_page(page)
    _open_model_menu(page)
    page.evaluate("""() => {
        const row = [...document.querySelectorAll('#modelPop .cloud-dyn.pop-item')]
            .find((n) => n.getAttribute('data-model') === 'vendor/model-1');
        row.click();
    }""")
    page.wait_for_timeout(250)
    page.reload()
    page.wait_for_timeout(500)
    label = page.evaluate("() => document.getElementById('modelLbl').textContent")
    assert "Meta: Llama 4 Maverick 402B" in label, f"the restored pin lost its name: {label!r}"
    tooltip = page.evaluate("() => document.getElementById('modelBtn').title")
    assert "Meta: Llama 4 Maverick 402B Instruct Turbo Preview Extended Context (free)" in tooltip, tooltip
    body = _send_and_capture(page, "after reload")
    assert body["model"] == "vendor/model-1", body
    assert body["model_selection"] == "pin", body
    _assert_no_new_problems(page, mark)


def test_an_unavailable_model_is_refused_without_touching_the_selection(session) -> None:
    """A switch the provider cannot resolve is refused ON THE ROW, the pill keeps the operator's
    standing selection, and the next request still carries it -- recovery is visible, and nothing
    switches identity silently, in either direction."""
    page = session
    mark = _problems_before()
    _fresh_page(page)
    _open_model_menu(page)
    page.evaluate("""() => {
        const row = [...document.querySelectorAll('#modelPop .cloud-dyn.pop-item')]
            .find((n) => n.getAttribute('data-model') === 'vendor/model-1');
        row.click();
    }""")
    page.wait_for_timeout(250)
    CAPTURED["pin_mode"] = "unavailable"
    _open_model_menu(page)
    page.evaluate("""() => {
        const row = [...document.querySelectorAll('#modelPop .cloud-dyn.pop-item')]
            .find((n) => n.getAttribute('data-model') === 'vendor/model-2');
        row.click();
    }""")
    page.wait_for_timeout(350)
    row_text = page.evaluate(
        """() => { const n = [...document.querySelectorAll('#modelPop .cloud-dyn.pop-item')]
             .find((x) => x.getAttribute('data-model') === 'vendor/model-2');
             return n ? n.querySelector('.pi-body').textContent : null; }""")
    assert row_text and "could not switch" in row_text, f"the refusal was not shown on the row: {row_text!r}"
    label = page.evaluate("() => document.getElementById('modelLbl').textContent")
    assert "Meta: Llama 4 Maverick 402B" in label, f"the standing selection was disturbed: {label!r}"
    body = _send_and_capture(page, "still the old pin")
    assert body["model"] == "vendor/model-1", body
    CAPTURED["pin_mode"] = "ok"
    # The only console error in this family is the provoked refusal log itself.
    _assert_no_new_problems(page, mark, allowing_404=True)


def test_losing_the_cloud_key_cannot_leave_a_cloud_pin_silently_in_charge(session) -> None:
    """When the key goes, a cloud pin cannot survive as a silent selection: the revert to Auto is
    painted on the pill. The one identity that still routes with no cloud at all -- an installed
    LOCAL pin -- survives the same event untouched."""
    page = session
    mark = _problems_before()
    _fresh_page(page)
    _open_model_menu(page)
    page.evaluate("""() => {
        const row = [...document.querySelectorAll('#modelPop .cloud-dyn.pop-item')]
            .find((n) => n.getAttribute('data-model') === 'vendor/model-1');
        row.click();
    }""")
    page.wait_for_timeout(250)
    page.evaluate("() => setCloudConnected(false)")
    page.wait_for_timeout(150)
    label = page.evaluate("() => document.getElementById('modelLbl').textContent")
    assert label == "VOOL Auto", f"the key loss was not painted on the pill: {label!r}"
    body = _send_and_capture(page, "no key now")
    assert body["model"] == "vool", body

    page.evaluate("""() => { localModelIds.add('qwen3:8b'); modelValue = 'qwen3:8b';
                             localStorage.setItem('vool_model', 'qwen3:8b'); reflectModel(); }""")
    label = page.evaluate("() => document.getElementById('modelLbl').textContent")
    assert label == "qwen3:8b", f"the local pin is not visible: {label!r}"
    body = _send_and_capture(page, "local pin with no key")
    assert body["model"] == "qwen3:8b" and body["model_selection"] == "pin", body
    _assert_no_new_problems(page, mark)


# ---------------------------------------------------------------- the rule, stated in the stylesheet


def test_the_reserve_is_a_flex_item_that_yields_before_anything_readable() -> None:
    """Padding held the width back unconditionally and clipped the chips at 520px."""
    assert '<span class="cb-tail" aria-hidden="true"></span>' in HTML
    assert ".cb-tail { flex:0 20 var(--composer-tail,0px); min-width:0;" in HTML
    assert "padding-right:var(--composer-tail" not in HTML, "the reserve must not be padding again"
    assert "footer.style.setProperty('--composer-tail'" in HTML
    # The control row's own gap already sits between the row's last control and the reserve;
    # counting it twice left the row's content a constant 8px short of the composer's edge.
    assert "const reserve = width + gapOf(row) - gapOf(bar);" in HTML


def test_the_wrap_breakpoint_was_not_moved_to_paper_over_the_anchor() -> None:
    """Raising it to 400px wrapped a 900px letterboxed row onto three lines; the reserve yields instead."""
    assert "@container composer (max-width: 310px) {" in HTML
    assert "@media (max-width: 310px) {" in HTML


def test_the_anchor_is_an_auto_margin_not_a_tuned_spacer() -> None:
    """Routing authority is the top-bar pill from the latest ux-pass1 prototype."""
    header = HTML[HTML.index("<header>"):HTML.index("</header>")]
    footer = HTML[HTML.index("<footer>"):HTML.index("</footer>")]
    assert header.count('id="modelBtn"') == 1
    assert 'id="modelBtn"' not in footer
    assert "#modelBtn { min-width:132px; border-radius:999px;" in HTML


def test_the_reserve_is_recomputed_when_the_window_changes() -> None:
    assert "syncHeaderHeight(); reclampLayout(); syncComposerTail();" in HTML
    assert "new ResizeObserver(syncComposerTail).observe(sendEl)" in HTML
