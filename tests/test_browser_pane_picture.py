"""The empty view of the Browser panel inside VOOL shows the picture of the hour.

What a person would ask of it: when I open the Browser in VOOL and no page is up, do I see
VOOL's picture and not a bare box, is it the hour I am in, can I still read what the panel
tells me and press its button, and did showing it ask anyone for anything. The panel is the
real page, served through the app's own doors, in a real engine.
"""
from __future__ import annotations

from core.i18n import catalog_for
from core.vool_browser import home as start
from core.vool_browser import live_page
from tests.first_run_pact_rig import pact_rig as pact_rig
from tests.test_browser_home import LANGUAGES
from tests.test_browser_pane_private_web import pane as pane
from tests.test_live_browser import world as world

LINES = ("home_point_memory", "home_point_cost", "home_point_local", "home_point_account", "home_point_ask", "home_point_forget")

#: The empty view at a given moment, as it draws itself when it comes up and every few
#: minutes after: what is drawn, and whether the panel's own words can be read on it.
_AT = """([y, m, d, h, min]) => {
  paintSky(new Date(y, m - 1, d, h, min));
  const shade = (colour) => { const [r, g, b] = colour.match(/[\\d.]+/g).slice(0, 3).map((v) => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * r + .7152 * g + .0722 * b; };
  const against = (id) => { const ink = shade(getComputedStyle(document.getElementById(id)).color), paper = shade(getComputedStyle(document.getElementById('blank')).backgroundColor); return (Math.max(ink, paper) + .05) / (Math.min(ink, paper) + .05); };
  const box = (id) => document.getElementById(id).getBoundingClientRect(), view = box('viewport'), button = box('blankWindow');
  const apart = (a, b) => a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top;
  const top = document.elementFromPoint((button.left + button.right) / 2, (button.top + button.bottom) / 2);
  return {
    part: document.getElementById('blank').dataset.sky || '', line: document.getElementById('point').innerText,
    sun: !!document.getElementById('sun'), moon: !!document.getElementById('moon'), stars: !!document.getElementById('stars'),
    flag: !!document.getElementById('face'), land: (document.getElementById('land') || {getAttribute: () => ''}).getAttribute('d'),
    title: document.getElementById('blankTitle').innerText, body: document.getElementById('blankBody').innerText,
    readable: [against('blankTitle'), against('blankBody'), against('blankWindow')],
    fills: box('sky').width >= view.width - 2 && box('sky').height >= view.height - 2,
    inside: ['blankTitle', 'blankBody', 'blankWindow', 'point'].every((id) => box(id).left >= view.left && box(id).right <= view.right && box(id).top >= view.top && box(id).bottom <= view.bottom),
    clear: apart(box('point'), document.querySelector('#blank .inner').getBoundingClientRect()),
    pressable: top === document.getElementById('blankWindow'), small: document.getElementById('blank').classList.contains('small'),
  };
}"""


def test_the_empty_browser_panel_shows_the_hour_as_vools_picture_and_keeps_its_words_and_its_button(pane):
    from playwright.sync_api import expect

    words = catalog_for("en")
    lines = {words.text("live." + key) for key in LINES}
    expect(pane.locator("#blankWindow")).to_be_visible()
    at = lambda *moment: pane.evaluate(_AT, list(moment))  # noqa: E731

    # As it came up, by the computer's own clock: the picture, and one of VOOL's six lines.
    opened = pane.evaluate("[document.getElementById('blank').dataset.sky, document.getElementById('point').innerText, !!document.getElementById('land'), !!document.getElementById('face')]")
    assert opened[0] in {"night", "first_light", "dawn", "day", "evening", "last_light"} and opened[1] in lines and opened[2] and opened[3], opened

    # One day, hour by hour: the same parts of the day the start page of the browser window shows.
    day = {hour: at(2031, 3, 4, hour, 0) for hour in (2, 5, 7, 12, 18, 20, 23)}
    assert [seen["part"] for seen in day.values()] == ["night", "first_light", "dawn", "day", "evening", "last_light", "night"]
    assert [(seen["sun"], seen["moon"], seen["stars"]) for seen in day.values()] == [
        (False, True, True), (False, True, True), (True, False, False), (True, False, False),
        (True, False, False), (False, True, True), (False, True, True)]
    for hour, seen in day.items():
        # The panel still says what it said, and it can be read on the picture at any hour.
        assert (seen["title"], seen["body"]) == (words.text("live.empty_title"), words.text("live.empty_body")), hour
        assert min(seen["readable"]) >= 7, (hour, seen["readable"])
        assert seen["flag"] and seen["fills"] and seen["inside"] and seen["clear"] and seen["pressable"], (hour, seen)
        assert seen["line"] in lines, hour
    # The land is the day's: the same at noon and at night, another the next day.
    assert day[12]["land"] == day[23]["land"] and day[12]["land"] != at(2031, 3, 5, 12, 0)["land"]
    # It is the very land the start page of the browser window draws for that day.
    assert live_page.PICTURE_JS is start.PICTURE_JS and start.PICTURE_JS in start._files("en")["home.js"]

    # A low or narrow panel: the words get paper of their own, and all of it still fits and reads.
    for size in ((700, 480), (1100, 520), (420, 700)):
        pane.set_viewport_size({"width": size[0], "height": size[1]})
        seen = at(2031, 3, 4, 12, 0)
        assert seen["small"] and seen["inside"] and seen["clear"] and seen["pressable"] and min(seen["readable"]) >= 7, (size, seen)
    pane.set_viewport_size({"width": 1100, "height": 800})
    assert at(2031, 3, 4, 12, 0)["small"] is False

    # Drawing it asked no one for anything: the page loaded nothing from outside itself.
    outside = pane.evaluate("() => performance.getEntriesByType('resource').map((entry) => entry.name).filter((name) => new URL(name).origin !== location.origin)")
    assert outside == [], outside


def test_a_picture_that_cannot_be_drawn_leaves_the_panel_as_it_was(pane):
    from playwright.sync_api import expect

    words = catalog_for("en")
    expect(pane.locator("#blankWindow")).to_be_visible()
    # An engine that cannot draw it: the drawing stops with a fault of its own.
    pane.evaluate("() => { picture.paint = () => { throw new Error('cannot draw'); }; }")
    seen = pane.evaluate(_AT, [2031, 3, 4, 12, 0])
    # No half picture, no line under it, no colours of the print: the panel's own words in the app's own colours.
    assert (seen["part"], seen["line"], seen["flag"]) == ("", "", False)
    assert (seen["title"], seen["body"]) == (words.text("live.empty_title"), words.text("live.empty_body"))
    assert seen["pressable"] and min(seen["readable"]) >= 4.5, seen
    # And the panel goes on working: the next reading of its state does not stop on it.
    pane.wait_for_timeout(1600)
    expect(pane.locator("#status")).to_have_text(words.text("live.ready"))


def test_the_panels_lines_are_the_start_pages_six_in_every_language():
    import json

    for language in LANGUAGES:
        page = live_page.render("live-test", language)
        config = json.loads(page.split("const cfg=", 1)[1].split(", $=id=>", 1)[0])
        assert config["points"] == [catalog_for(language).text("live." + key) for key in LINES], language
        assert len(set(config["points"])) == 6, language
