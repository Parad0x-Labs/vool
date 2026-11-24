"""The starting profile: settings the engine guards, made by the engine itself.

A real engine makes it here in a hidden run of its own, and what is in force afterwards is
read from the engine's own settings page, never from a file VOOL wrote. What a person
would ask: is my search the one VOOL promised and not the engine maker's, is the home
button there, did making it reach anyone, and does the browser still open when the engine
cannot make it.
"""
from __future__ import annotations

import json
import time

from core.vool_browser import desk as desk_module
from core.vool_browser import start_profile
from tests.test_desk_browser import REAL_READY, _wait
from tests.test_desk_browser import browser as browser
from tests.test_desk_browser import home as home
from tests.test_desk_browser import site as site

DUCK = ["DuckDuckGo", "https://duckduckgo.com/?q=%s"]


def in_force(browser) -> dict:
    """What the engine's own settings page says is in force in the lasting window."""

    async def read():
        page = await browser.context_for_agent().new_page()
        try:
            await page.goto(start_profile.SETTINGS_PAGE)
            return await page.evaluate(start_profile.READ)
        finally:
            await page.close()

    return browser._call(read())


def kept_search(profile) -> str:
    """The search address the engine keeps for a profile, from whichever file holds it."""
    for name in ("Secure Preferences", "Preferences"):
        try:
            written = json.loads((profile / "Default" / name).read_text())
        except (OSError, ValueError):
            continue
        data = (written.get("default_search_provider_data") or {}).get("template_url_data") or {}
        if data.get("url"):
            return data["url"]
    return ""


def test_the_engine_itself_holds_the_starting_search_and_home_button_and_made_them_reaching_no_one(
    browser, site, monkeypatch, tmp_path
):
    base, hits = site
    # The engine makes the starting profile afresh here, and writes down every connection it
    # tries during that hidden run. The window that follows writes to the same record.
    record = tmp_path / "connections.json"
    monkeypatch.setattr(start_profile, "ready", REAL_READY)
    monkeypatch.setattr(desk_module, "extra_engine_flags", (*desk_module.extra_engine_flags, f"--log-net-log={record}"))
    seen: dict = {}
    real_make = start_profile._make

    async def make_and_look(binary, work):
        why = await real_make(binary, work)
        seen["record"] = json.loads(record.read_text())
        return why

    monkeypatch.setattr(start_profile, "_make", make_and_look)

    browser.open(base + "/first")
    assert _wait(lambda: any(path == "/first" for path, _ in hits))
    assert in_force(browser) == {"search": [DUCK], "home_button": True}
    binary = desk_module.engine_binary()
    assert start_profile.problem(binary) == ""

    # Every connection the hidden run tried went to the one address on this machine where
    # nothing listens. The record is the engine's own; it lists each attempt by address.
    kinds = {number: name for name, number in seen["record"]["constants"]["logEventTypes"].items()}
    tried = sorted({
        str(event["params"]["address"]) for event in seen["record"]["events"]
        if "CONNECT" in kinds.get(event["type"], "") and isinstance(event.get("params"), dict) and "address" in event["params"]
    })
    assert tried == ["127.0.0.1:9"], tried

    # The starting profile holds settings files and nothing else of that run.
    start = start_profile.folder(binary)
    assert {f.name for f in (start / "Default").iterdir()} <= set(start_profile.KEEP)
    assert [f.name for f in start.iterdir()] == ["Default"]
    assert [f.name for f in desk_module._root().glob("start-*")] == [start.name]

    # It lasts: the engine reads the same after a restart, and the person's own change wins.
    browser.stop()
    browser.open("")
    assert in_force(browser) == {"search": [DUCK], "home_button": True}

    async def switch_home_button_off():
        page = await browser.context_for_agent().new_page()
        await page.goto("chrome://settings/appearance")
        await page.evaluate("() => new Promise((done) => chrome.settingsPrivate.setPref('browser.show_home_button', false, '', done))")
        await page.close()

    browser._call(switch_home_button_off())
    browser.stop()
    browser.open("")
    assert in_force(browser) == {"search": [DUCK], "home_button": False}


def test_a_private_window_begins_with_the_same_search_without_a_second_hidden_run(browser, site, monkeypatch):
    base, hits = site
    runs: list[str] = []
    real_make = start_profile._make

    async def counted(binary, work):
        runs.append(binary)
        return await real_make(binary, work)

    monkeypatch.setattr(start_profile, "ready", REAL_READY)
    monkeypatch.setattr(start_profile, "_make", counted)
    browser.open(base + "/lasting")
    assert len(runs) == 1
    browser.open(base + "/private", private=True)
    assert _wait(lambda: any(path == "/private" for path, _ in hits))
    private = sorted(desk_module._root().glob("private-*"))
    assert len(private) == 1
    assert kept_search(private[0]) == desk_module.STARTING_SEARCH["url"]
    assert kept_search(desk_module.profile_dir()) == desk_module.STARTING_SEARCH["url"]
    # One hidden run served both profiles.
    assert len(runs) == 1


def test_another_starting_search_gets_a_starting_profile_of_its_own_and_the_old_one_goes(browser, site, monkeypatch):
    base, _ = site
    monkeypatch.setattr(start_profile, "ready", REAL_READY)
    browser.open("")
    binary = desk_module.engine_binary()
    first = start_profile.folder(binary)
    assert first.is_dir()
    # A later VOOL starts new profiles with another search. The person's lasting profile
    # keeps the search it has; a private window, which is new every time, begins with the new one.
    monkeypatch.setitem(desk_module.STARTING_SEARCH, "name", "Local search")
    monkeypatch.setitem(desk_module.STARTING_SEARCH, "keyword", "local")
    monkeypatch.setitem(desk_module.STARTING_SEARCH, "url", base + "/search?q={searchTerms}")
    browser.open(base + "/private", private=True)
    second = start_profile.folder(binary)
    assert second != first and second.is_dir() and not first.exists()
    assert kept_search(sorted(desk_module._root().glob("private-*"))[0]) == base + "/search?q={searchTerms}"
    assert in_force(browser)["search"] == [DUCK]


def test_the_browser_still_opens_when_the_engine_cannot_make_a_starting_profile(browser, site, monkeypatch):
    base, hits = site
    monkeypatch.setattr(start_profile, "ready", REAL_READY)
    # This engine's settings page has no way to add a search: the page asked for does not exist.
    monkeypatch.setattr(start_profile, "SETTINGS_PAGE", "chrome://settings/there-is-no-such-page")
    monkeypatch.setattr(start_profile, "_PAGE_WAIT_MS", 1500)
    browser.open(base + "/first")
    assert _wait(lambda: any(path == "/first" for path, _ in hits))
    binary = desk_module.engine_binary()
    assert start_profile.problem(binary) == "no_add_button"
    assert list(desk_module._root().glob("start-*")) == []
    # VOOL's own settings are in the profile all the same.
    written = json.loads((desk_module.profile_dir() / "Default" / "Preferences").read_text())
    assert written["https_only_mode_enabled"] is True and written["profile"]["cookie_controls_mode"] == 1
    # A second new profile in the same run of the app does not try again.
    began = time.monotonic()
    assert start_profile.problem(binary) == "no_add_button"
    browser._call(start_profile.ready(binary))
    assert time.monotonic() - began < 1
