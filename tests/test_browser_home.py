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


def test_the_start_page_is_written_afresh_at_every_start_and_in_the_language_vool_was_last_seen_in(browser, site):
    browser.open("")
    folder = next(start._root().iterdir())
    browser.stop()
    # Changed on disk while the browser was closed, and VOOL since seen in another language.
    (folder / "home.js").write_text("document.title = 'not VOOL';")
    guard.remember_language("lt")
    browser.open("")
    page = next(p for p in browser.context_for_agent().pages if not p.is_closed())

    async def look():
        await page.wait_for_selector("#q")
        return await page.title(), await page.get_attribute("#q", "placeholder"), await page.evaluate("document.documentElement.lang")

    words = catalog_for("lt")
    assert browser._call(look()) == (words.text("live.home_title"), words.text("live.home_search"), "lt")
    assert [path.name for path in start._root().iterdir()] != [folder.name]
    assert len(list(start._root().iterdir())) == 1


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
