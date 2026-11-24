"""VOOL's start page in the browser window: a real engine, a real window, real local pages.

What a person would ask of it: is a new tab VOOL's page in my language, does its search box
use the search I chose, does an address typed there open as typed, are my most visited
sites there, and can the page reach anything it should not.
"""
from __future__ import annotations

import json

import pytest

from core.i18n import catalog_for
from core.vool_browser import desk as desk_module
from core.vool_browser import guard
from core.vool_browser import home as start
from tests.test_desk_browser import _wait, start_page
from tests.test_desk_browser import browser as browser
from tests.test_desk_browser import home as home
from tests.test_desk_browser import site as site

LANGUAGES = ("ar", "de", "en", "es", "fr", "he", "hi", "ja", "ko", "lt", "pl", "pt", "ru", "tr", "uk", "vi", "zh-Hans")


def asked(hits):
    return [path for path, _ in hits if path != "/favicon.ico"]


def new_tab(browser):
    async def make():
        page = await browser.context_for_agent().new_page()
        await page.goto("chrome://newtab/")
        await page.wait_for_selector("#q")
        return page

    return browser._call(make())


def test_a_new_tab_is_vools_start_page_and_its_box_uses_the_search_the_person_has(browser, site, monkeypatch):
    base, hits = site
    # The search this person's browser has is a site on this machine, so the test can see it asked.
    monkeypatch.setitem(desk_module.STARTING_SEARCH, "url", base + "/search?q={searchTerms}")
    words = catalog_for("en")
    browser.open("")
    first = next(p for p in browser.context_for_agent().pages if not p.is_closed())
    assert first.url == start_page(browser)

    async def look(page):
        await page.wait_for_selector("#q")
        return (await page.title(), await page.get_attribute("#q", "placeholder"),
                await page.evaluate("document.activeElement.id"), await page.is_hidden("#visited"),
                await page.inner_text("main"))

    title, hint, focus, nothing_visited, text = browser._call(look(first))
    assert (title, hint, focus) == (words.text("live.home_title"), words.text("live.home_search"), "q")
    # Nothing visited yet: no list, and the engine's own store is not passed off as a visited site.
    assert nothing_visited and "Web Store" not in text
    assert asked(hits) == []

    async def type_in(page, text):
        await page.fill("#q", text)
        await page.press("#q", "Enter")

    # Words are searched for with the person's search, in the same tab.
    browser._call(type_in(first, "night trains to the coast"))
    assert _wait(lambda: asked(hits) == ["/search?q=night+trains+to+the+coast"]), asked(hits)
    assert first.url == base + "/search?q=night+trains+to+the+coast"
    # An address is opened as typed, whole or without its "http://", and is not searched for.
    second = new_tab(browser)
    assert second.url == start_page(browser)
    browser._call(type_in(second, base + "/whole"))
    third = new_tab(browser)
    browser._call(type_in(third, base.removeprefix("http://") + "/bare"))
    assert _wait(lambda: asked(hits)[1:] == ["/whole", "/bare"]), asked(hits)
    # An empty box does nothing.
    fourth = new_tab(browser)
    browser._call(type_in(fourth, "   "))
    assert fourth.url == start_page(browser) and len(asked(hits)) == 3


SHAPES = [
    ("example.org", "https://example.org/"),
    ("example.org/fares?from=a", "https://example.org/fares?from=a"),
    ("https://example.org/x", "https://example.org/x"),
    ("http://example.org/x", "http://example.org/x"),
    ("localhost:8080/admin", "http://localhost:8080/admin"),
    ("192.168.1.1", "http://192.168.1.1/"),
    ("bücher.example", "https://xn--bcher-kva.example/"),
    # Words, numbers and anything that is not a web address are searched for, never opened.
    ("night trains", ""),
    ("3.14", ""),
    ("e.g.", ""),
    ("what is example.org", ""),
    ("javascript:alert(1)", ""),
    ("file:///etc/hosts", ""),
    ("chrome://settings", ""),
    ("someone@example.org", ""),
]


def test_the_box_tells_an_address_from_words_by_its_shape(browser):
    browser.open("")
    page = next(p for p in browser.context_for_agent().pages if not p.is_closed())

    async def read():
        await page.wait_for_selector("#q")
        return await page.evaluate("(typed) => typed.map((text) => address(text))", [typed for typed, _ in SHAPES])

    assert list(zip([typed for typed, _ in SHAPES], browser._call(read()), strict=True)) == SHAPES


def test_the_persons_most_visited_sites_are_on_the_start_page(browser, site):
    base, hits = site
    browser.open(base + "/timetable")
    assert _wait(lambda: asked(hits) == ["/timetable"])
    browser.stop()
    hits.clear()
    browser.open("")
    page = next(p for p in browser.context_for_agent().pages if not p.is_closed())

    async def sites():
        await page.wait_for_selector("#sites a", timeout=10000)
        return (await page.inner_text("#visitedTitle"), await page.get_attribute("#sites a", "href"),
                await page.inner_text("#sites a span"), await page.inner_text("#sites a small"),
                await page.evaluate("document.querySelector('#sites img').naturalWidth > 0"))

    title, link, name, host, has_icon = browser._call(sites())
    assert (title, link, name, host) == (catalog_for("en").text("live.home_visited"), base + "/timetable", "Timetable", "127.0.0.1")
    # The icon is drawn from what the engine already holds: showing the list asked the site for nothing.
    assert has_icon and hits == []


def test_the_list_is_up_to_date_in_a_new_tab_puts_the_most_visited_site_first_and_shows_no_search_words(browser, site):
    base, hits = site
    other = base.replace("127.0.0.1", "localhost")
    # One visit to one site; then on another site a page opened twice and a search opened three times.
    browser.open(other + "/news")
    for _ in range(2):
        browser.open(base + "/timetable")
    for _ in range(3):
        browser.open(base + "/find?q=something-private")
    assert _wait(lambda: len([path for path, _ in hits if path.startswith("/find")]) == 3)

    async def shown():
        # A new tab, in the same run of the browser: no restart is needed for the list to be right.
        page = await browser.context_for_agent().new_page()
        await page.goto(desk_module.HOME)
        await page.wait_for_selector("#sites a")
        links = await page.evaluate("[...document.querySelectorAll('#sites a')].map((a) => a.href)")
        text = await page.inner_text("#sites")
        await page.close()
        return links, text

    links, text = browser._call(shown())
    # The site with five visits comes first, and its link is the page opened most that carries no question part.
    assert links == [base + "/timetable", other + "/news"], links
    assert "private" not in text and "find" not in text


def test_the_start_page_is_checked_at_every_start_and_written_in_the_language_vool_was_last_seen_in(browser, site):
    browser.open("")
    folder = next(start._root().iterdir())
    address = start_page(browser)
    written = (folder / "home.js").stat().st_mtime_ns
    browser.stop()
    # Nothing changed: the folder is left as it is, and the page keeps its address.
    browser.open("")
    assert (folder / "home.js").stat().st_mtime_ns == written and start_page(browser) == address
    browser.stop()
    # Changed on disk while the browser was closed, and VOOL since seen in another language.
    (folder / "home.js").write_text("document.title = 'not VOOL';")
    (folder / "extra.js").write_text("document.title = 'not VOOL either';")
    guard.remember_language("lt")
    browser.open("")
    page = next(p for p in browser.context_for_agent().pages if not p.is_closed())

    async def look():
        await page.wait_for_selector("#q")
        return await page.title(), await page.get_attribute("#q", "placeholder"), await page.evaluate("document.documentElement.lang")

    words = catalog_for("lt")
    assert browser._call(look()) == (words.text("live.home_title"), words.text("live.home_search"), "lt")
    # One folder, always the same one, holding what VOOL writes and nothing else. The
    # page keeps its address in the engine, so what the engine remembers about it lasts.
    assert [path.name for path in start._root().iterdir()] == [folder.name]
    assert sorted(path.name for path in folder.iterdir()) == sorted(start._files("lt"))
    assert start_page(browser) == address


def hello(page):
    async def read():
        await page.wait_for_selector("#q")
        return (await page.is_hidden("#hello"), await page.inner_text("#helloTitle") if await page.is_visible("#hello") else "",
                await page.inner_text("#helloNote") if await page.is_visible("#hello") else "")

    return read()


def test_the_start_page_welcomes_the_person_once_and_greets_them_once_a_day(browser, site):
    base, hits = site
    words = catalog_for("en")
    browser.open("")
    first = next(p for p in browser.context_for_agent().pages if not p.is_closed())
    # The first time the page is ever shown: a welcome.
    assert browser._call(hello(first)) == (False, words.text("live.home_welcome"), words.text("live.home_welcome_note"))
    # Not again in the next tab, nor after the browser was closed and opened the same day.
    assert browser._call(hello(new_tab(browser))) == (True, "", "")
    browser.stop()
    browser.open("")
    again = next(p for p in browser.context_for_agent().pages if not p.is_closed())
    assert browser._call(hello(again)) == (True, "", "")

    # Another day: the first start page shown greets by the time of day and names the date.
    # The page is asked what it would show at a given moment, as it asks itself when it opens.
    async def on(year, month, day, hour):
        await again.evaluate(
            "([y, m, d, h]) => { document.getElementById('hello').hidden = true; hello(new Date(y, m - 1, d, h, 30)); }",
            [year, month, day, hour])
        seen = await again.is_visible("#hello")
        return (not seen, await again.inner_text("#helloTitle") if seen else "", await again.inner_text("#helloNote") if seen else "")

    hidden, title, note = browser._call(on(2031, 3, 4, 9))
    assert (hidden, title) == (False, words.text("live.home_morning"))
    assert note == "Tuesday, March 4" or sorted(note.replace(",", "").split()) == ["4", "March", "Tuesday"], note
    # Later the same day: nothing. The days after: by the hour.
    assert browser._call(on(2031, 3, 4, 15)) == (True, "", "")
    assert browser._call(on(2031, 3, 5, 15))[1] == words.text("live.home_afternoon")
    assert browser._call(on(2031, 3, 6, 21))[1] == words.text("live.home_evening")
    assert browser._call(on(2031, 3, 7, 2))[1] == words.text("live.home_evening")
    assert browser._call(on(2031, 3, 8, 5))[1] == words.text("live.home_morning")
    assert browser._call(on(2031, 3, 9, 12))[1] == words.text("live.home_afternoon")
    # The day last greeted is kept with the profile: a new tab on that day shows no greeting.
    browser._call(again.evaluate("() => { const d = new Date(); localStorage.setItem('hello', JSON.stringify({day: [d.getFullYear(), d.getMonth() + 1, d.getDate() - 1].join('-')})); }"))
    hidden, title, note = browser._call(hello(new_tab(browser)))
    assert not hidden and title in {words.text("live.home_" + part) for part in ("morning", "afternoon", "evening")} and note
    assert browser._call(hello(new_tab(browser))) == (True, "", "")
    # None of it asked anyone for anything.
    assert asked(hits) == []


def test_a_place_is_kept_on_the_start_page_for_top_skills_and_plugins_and_shows_nothing_yet(browser):
    browser.open("")
    page = next(p for p in browser.context_for_agent().pages if not p.is_closed())

    async def look():
        await page.wait_for_selector("#q")
        return await page.is_hidden("#picks"), await page.inner_html("#picks"), await page.evaluate(
            "[...document.querySelectorAll('main > *')].map((el) => el.id || el.tagName)")

    assert browser._call(look()) == (True, "", ["H1", "hello", "search", "visited", "picks"])


def test_a_private_window_is_the_engines_own_private_kind_and_keeps_the_engines_page(browser, site):
    browser.open("", private=True)
    window = browser._private[0]

    async def sides():
        session = await window.engine.browser.new_browser_cdp_session()
        try:
            private_sides = (await session.send("Target.getBrowserContexts"))["browserContextIds"]
            tabs = [t for t in (await session.send("Target.getTargets"))["targetInfos"] if t["type"] == "page"]
        finally:
            await session.detach()
        return private_sides, [tab.get("browserContextId") for tab in tabs]

    # The engine says so itself: the one tab is in a private side of it, which keeps
    # nothing, and not in the side that writes to a profile.
    private_sides, tab_sides = browser._call(sides())
    assert len(tab_sides) == 1 and tab_sides[0] in private_sides, (private_sides, tab_sides)
    urls = [p.url for p in window.context.pages if not p.is_closed()]
    assert len(urls) == 1 and urls[0].startswith(("chrome://newtab", "chrome://new-tab-page")), urls
    assert urls[0] != start.address(window)

    async def new_tab(side):
        page = await side.new_page()
        await page.goto("chrome://newtab/")
        return page

    # A further tab there is the same page of the engine's, whichever search it holds.
    again = browser._call(new_tab(window.context))
    assert again.url.startswith(("chrome://newtab", "chrome://new-tab-page")) and again.url != start.address(window)
    # An ordinary window the person opens from the private one gets VOOL's start page:
    # no new tab there is fetched from a search company either.
    ordinary = browser._call(new_tab(window.engine))
    assert ordinary.url == start.address(window)
    assert browser._call(ordinary.title()) == catalog_for("en").text("live.home_title") != browser._call(again.title())
    # It is part of the private window: that is over once no tab is left on either side.
    browser._call(again.close())
    browser._call(next(p for p in window.context.pages if not p.is_closed()).close())
    assert window.alive() and window.profile.exists()
    browser._call(ordinary.close())
    assert _wait(lambda: not window.profile.exists())

    # Opened on an address, a private window shows that page and no tab beside it.
    base, hits = site
    browser.open(base + "/quiet", private=True)
    assert _wait(lambda: asked(hits) == ["/quiet"])
    assert [p.url for p in browser._private[-1].context.pages if not p.is_closed()] == [base + "/quiet"]
    assert [p.url for p in browser._private[-1].engine.pages if not p.is_closed()] == []


def test_a_start_page_that_cannot_be_written_does_not_keep_the_browser_from_opening(browser, site, monkeypatch):
    base, hits = site

    def no_room(locale=""):
        raise OSError("no space left")

    monkeypatch.setattr(start, "build", no_room)
    browser.open(base + "/wanted")
    assert _wait(lambda: asked(hits) == ["/wanted"])
    assert browser._main.home_id == ""


def test_the_start_page_can_reach_nothing_but_the_engines_own_lists(home):
    files = start._files("en")
    manifest = json.loads(files["manifest.json"])
    assert manifest["permissions"] == ["history", "search", "favicon"]
    for reach in ("host_permissions", "optional_permissions", "optional_host_permissions", "content_scripts",
                  "background", "externally_connectable", "web_accessible_resources"):
        assert reach not in manifest, reach
    assert manifest["chrome_url_overrides"] == {"newtab": "home.html"}
    script = files["home.js"]
    for way_out in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource", "importScripts", "eval("):
        assert way_out not in script, way_out
    # The page loads only its own two files.
    assert files["home.html"].count("src=") == 1 and 'src="home.js"' in files["home.html"]
    assert files["home.html"].count("href=") == 1 and 'href="home.css"' in files["home.html"]
    assert "http" not in files["home.css"] and "url(" not in files["home.css"]


def test_the_start_page_speaks_every_language_vool_has(home):
    english = {key: catalog_for("en").text("live." + key) for key in start._WORDS}
    seen = set()
    for language in LANGUAGES:
        words = {key: catalog_for(language).text("live." + key) for key in start._WORDS}
        files = start._files(language)
        assert json.loads(files["manifest.json"])["name"] == words["home_name"]
        for key, text in words.items():
            assert text.strip() and json.dumps(text, ensure_ascii=False) in files["home.js"], (language, key)
            if language != "en" and key != "home_name":
                assert text != english[key], (language, key)
        assert ('const DIRECTION = "rtl"' in files["home.js"]) == (language in ("ar", "he")), language
        seen.add(tuple(words.values()))
    assert len(seen) == len(LANGUAGES)
