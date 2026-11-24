"""The picture on VOOL's start page: a real engine, a real window, the page as VOOL writes it.

What a person would ask of it: does the page show the hour I am in, can I read the words
on it at any hour, is the line under it one of VOOL's own, does it stay the same land for
a day, and did drawing it ask anyone for anything.
"""
from __future__ import annotations

from core.i18n import catalog_for
from core.vool_browser import guard
from core.vool_browser import home as start
from tests.test_browser_home import LANGUAGES, asked
from tests.test_desk_browser import browser as browser
from tests.test_desk_browser import home as home
from tests.test_desk_browser import site as site

LINES = ("home_point_memory", "home_point_cost", "home_point_local", "home_point_account", "home_point_ask", "home_point_forget")

#: What the page is asked at a given moment, as it asks itself when it opens and every
#: few minutes after: what is drawn, where, and whether the words can be read on it.
_AT = """([y, m, d, h, min]) => {
  scene(new Date(y, m - 1, d, h, min));
  const shade = (colour) => { const [r, g, b] = colour.match(/[\\d.]+/g).slice(0, 3).map((v) => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * r + .7152 * g + .0722 * b; };
  const ink = shade(getComputedStyle(document.querySelector('h1')).color), paper = shade(getComputedStyle(document.body).backgroundColor);
  const disc = document.querySelector('#sun circle, #moon circle'), moon = document.getElementById('moon');
  return {
    part: document.documentElement.dataset.sky, line: document.getElementById('point').innerText,
    sun: !!document.getElementById('sun'), moon: !!moon, stars: !!document.getElementById('stars'), flag: !!document.getElementById('face'),
    across: Number(disc.getAttribute('cx')), up: -Number(disc.getAttribute('cy')), age: moon ? Number(moon.dataset.age) : null,
    land: document.getElementById('land').getAttribute('d'), readable: (Math.max(ink, paper) + .05) / (Math.min(ink, paper) + .05),
    drawn: document.getElementById('sky').getBoundingClientRect().width >= innerWidth,
  };
}"""


def first_page(browser):
    browser.open("")
    page = next(p for p in browser.context_for_agent().pages if not p.is_closed())
    browser._call(page.wait_for_selector("#q"))
    return page


def test_the_start_page_shows_the_hour_as_a_picture_under_one_of_vools_lines(browser, site):
    base, hits = site
    words = catalog_for("en")
    lines = {words.text("live." + key) for key in LINES}
    page = first_page(browser)
    at = lambda *moment: browser._call(page.evaluate(_AT, list(moment)))  # noqa: E731

    # As it opened, by the computer's own clock: a picture, one of the six lines, and the box ready to type in.
    opened = browser._call(page.evaluate(
        "() => [document.documentElement.dataset.sky, document.getElementById('sky').childElementCount > 3,"
        " document.getElementById('point').innerText, document.activeElement.id]"))
    assert opened[0] in {"night", "first_light", "dawn", "day", "evening", "last_light"} and opened[1], opened
    assert opened[2] in lines and opened[3] == "q", opened

    # One day, hour by hour: the part of the day, and the sun by day or the moon and stars by night.
    day = {hour: at(2031, 3, 4, hour, 0) for hour in (2, 5, 7, 12, 18, 20, 23)}
    assert [seen["part"] for seen in day.values()] == ["night", "first_light", "dawn", "day", "evening", "last_light", "night"]
    assert [(seen["sun"], seen["moon"], seen["stars"]) for seen in day.values()] == [
        (False, True, True), (False, True, True), (True, False, False), (True, False, False),
        (True, False, False), (False, True, True), (False, True, True)]
    # The sun crosses: further along each time, and higher at noon than in the morning or the evening.
    assert day[7]["across"] < day[12]["across"] < day[18]["across"]
    assert day[12]["up"] > day[7]["up"] and day[12]["up"] > day[18]["up"]
    # The moon too, from last light through the night to first light.
    assert day[20]["across"] < day[23]["across"] and day[2]["across"] < day[5]["across"]
    # VOOL's flag stands in every one, the picture fills the window, and the words can be
    # read on it at any hour (well past what small print asks for).
    for hour, seen in day.items():
        assert seen["flag"] and seen["drawn"] and seen["readable"] >= 7, (hour, seen["readable"])
    # One line about VOOL under it, another for each part of the day, all six in a day.
    assert {seen["line"] for seen in day.values()} == lines
    assert day[2]["line"] == day[23]["line"]
    # The land is that day's from midnight to midnight, and another the next day.
    assert len({seen["land"] for seen in day.values()}) == 1
    assert at(2031, 3, 5, 12, 0)["land"] != day[12]["land"]
    assert at(2031, 3, 4, 12, 0)["land"] == day[12]["land"]

    # The moon is as full as it is that night: full on 24 November 2026, new on the 9th,
    # and half way between on the 17th. A day either side is allowed for where the computer is.
    assert abs(at(2026, 11, 24, 23, 0)["age"] - .5) < .04
    assert min(at(2026, 11, 9, 23, 0)["age"], 1 - at(2026, 11, 9, 23, 0)["age"]) < .04
    assert abs(at(2026, 11, 17, 23, 0)["age"] - .27) < .05

    # Drawing it asked no one for anything: the page loaded nothing from outside itself, and the site saw nothing.
    outside = browser._call(page.evaluate("() => performance.getEntriesByType('resource').map((entry) => entry.name).filter((name) => new URL(name).origin !== location.origin)"))
    assert outside == [], outside
    assert asked(hits) == []


def test_in_a_small_window_the_words_get_paper_of_their_own_and_the_line_stays_under_them(browser, site):
    page = first_page(browser)

    async def laid_out(width, height):
        await page.set_viewport_size({"width": width, "height": height})
        return await page.evaluate(
            "() => { const main = document.querySelector('main'), around = main.getBoundingClientRect(), point = document.getElementById('point').getBoundingClientRect(),"
            " box = document.getElementById('q').getBoundingClientRect();"
            " return [point.top >= around.bottom - 1, box.left >= 0 && box.right <= innerWidth, document.documentElement.scrollWidth <= innerWidth,"
            " getComputedStyle(main).backgroundColor === getComputedStyle(document.body).backgroundColor]; }")

    # A roomy window: the words stand on the sky itself.
    assert browser._call(laid_out(1280, 720)) == [True, True, True, False]
    # A low or a narrow one, where the land would run under them: they stand on paper of their own.
    for size in ((700, 420), (1280, 500), (380, 640)):
        assert browser._call(laid_out(*size)) == [True, True, True, True], size


def test_where_the_language_reads_from_the_right_the_picture_is_turned_and_the_flags_face_is_not(browser, site):
    def face_and_side(language):
        guard.remember_language(language)
        page = first_page(browser)
        browser._call(page.set_viewport_size({"width": 1280, "height": 720}))
        seen = browser._call(page.evaluate(
            "() => { const face = document.getElementById('face').getBoundingClientRect(), eyes = [...document.querySelectorAll('#face circle')].slice(0, 2).map((eye) => eye.getBoundingClientRect());"
            " return {side: (face.left + face.right) / 2 > innerWidth / 2, big: eyes[0].width > eyes[1].width, bigFirst: eyes[0].left < eyes[1].left,"
            " words: document.getElementById('q').getBoundingClientRect().left > innerWidth - document.getElementById('q').getBoundingClientRect().right, line: document.getElementById('point').innerText}; }"))
        browser.stop()
        return seen

    plain, turned = face_and_side("en"), face_and_side("he")
    # Left to right: the words on the left, the flag on the right. Right to left: the other way round.
    assert (plain["words"], plain["side"]) == (False, True) and (turned["words"], turned["side"]) == (True, False)
    # The face on the flag reads the same way in both: its larger eye is on the same side.
    assert plain["big"] and turned["big"] and plain["bigFirst"] is True and turned["bigFirst"] is True
    assert turned["line"] in {catalog_for("he").text("live." + key) for key in LINES}
    assert turned["line"] not in {catalog_for("en").text("live." + key) for key in LINES}


def test_the_six_lines_are_six_different_ones_in_every_language_and_the_page_holds_no_other(home):
    for language in LANGUAGES:
        words = [catalog_for(language).text("live." + key) for key in LINES]
        assert len(set(words)) == 6 and all(text.strip() for text in words), language
        script = start._files(language)["home.js"]
        named = script.split("const POINTS = [", 1)[1].split("]", 1)[0]
        assert [name.strip(" '") for name in named.split(",")] == list(LINES), language
