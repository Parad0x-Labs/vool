"""The everyday browser: a real window with a lasting profile of its own.

The supervised view (``live.py``) shows a picture of one page per chat and deletes
its profile on close. This is the other half: a full browser the person uses
directly, with tabs, page scripts, bookmarks, history, extensions and saved
sign-ins, which VOOL's agent can also work in.

Laws:

- **Off until switched on.** Nothing starts and no request leaves while the Browser
  switch is off, web lookups are off, or local-only mode is on.
- **Its own profile, always.** The profile lives under the active VOOL data folder
  and can never be the person's Chrome, Safari, Edge or Firefox profile.
- **Private window leaves nothing.** It runs on a throwaway profile of its own,
  off the record, and that folder is removed when the window closes. If it cannot
  be removed the window reports failure instead of pretending.
- **Saved sign-ins use the system's credential store.** The mock keychain and the
  plain store that the supervised view uses on purpose are never passed here.
- **No open control port.** VOOL talks to the engine over a pipe it owns; no other
  program on the machine can attach.
- **Private web is honoured or nothing is sent.** With a route on, the engine gets
  that route as its only way out and resolves names at the far end; a route that
  is down refuses the launch.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from core.runtime_paths import active_data_dir
from core.secret_env import child_env

from .engine import MAKER_UPDATE_HOST, QUIET_FLAGS, UNATTENDED_UI_FLAGS, find_engine_binary, refuse_real_profile_target

ENGINE_START_TIMEOUT = 30.0
#: While a window is open, how often the desk looks whether today's site lists are due.
LISTS_LOOK_SECONDS = 60 * 60
STOP_TIMEOUT = 15.0

#: Flags the engine handle adds by default that must never reach this browser. The
#: launch passes its own complete list, so none of them can; the guard test checks
#: the running engine's arguments against this tuple.
NEVER_PASSED: tuple[str, ...] = (
    "--enable-automation",          # the "controlled by automated software" bar
    "--disable-extensions",         # extensions are a feature here
    "--password-store=basic",       # would keep saved sign-ins in a store any program can read
    "--use-mock-keychain",          # same, on macOS
    "--disable-popup-blocking",     # the popup blocker stays on
    "--no-sandbox",
    "--mute-audio",
    "--hide-scrollbars",
)

#: Empty in the product. The test suite puts the engine's dummy credential store here
#: so that an unattended run can never raise a Keychain or keyring window; nothing in
#: the product reads an environment variable or a setting into it.
extra_engine_flags: tuple[str, ...] = ()


class DeskFault(ValueError):
    """A refusal with a stable code; the page turns the code into a plain sentence."""


def _root() -> Path:
    return active_data_dir() / "browser" / "desk"


def profile_dir() -> Path:
    return _root() / "profile"


def engine_binary() -> str | None:
    """VOOL's own engine when it has one, else a Chromium-family browser on the machine."""
    owned = active_data_dir() / "browser" / "engine"
    names = (
        ("Chromium.app/Contents/MacOS/Chromium",) if sys.platform == "darwin" else ("chrome", "chromium")
    )
    for name in names:
        candidate = owned / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return find_engine_binary()


def downloads_dir() -> Path:
    """Where the browser saves files: the person's own Downloads, never VOOL's data."""
    return Path.home() / "Downloads" / "VOOL Downloads"


#: The search a new profile starts with. Measured on the engine: with its built-in default
#: it asks that search company's site for something at every start and builds each new tab
#: from that company's servers. The engine itself puts this one in force: see
#: ``start_profile``. Some engines keep it as an entry of the person's own, with no new-tab
#: address; a newer one recognises the address and holds its own built-in entry for that
#: search instead, which names a page of that search's site for new tabs. No new tab here
#: is that page either way: the lasting window's is VOOL's start page, and a private
#: window's is the engine's own page for private windows.
STARTING_SEARCH: dict[str, str] = {
    "name": "DuckDuckGo",
    "keyword": "duckduckgo",
    "url": "https://duckduckgo.com/?q={searchTerms}",
}


#: Where the window starts when no address was asked for: the new tab page, which is
#: VOOL's start page in the lasting window and the engine's own page in a private one.
HOME = "chrome://newtab/"
#: The engine is started without a window. It pins its start-up notices to the tabs of the
#: window it starts with, and one of them names the launch options that keep name lookups
#: on the Private web route and the engine's sign-in service away. When the person chose to
#: continue where they left off, that window holds their own tabs. An engine that starts
#: with no window has no tab to pin a notice on; its first window is made once site
#: protection, the start page and the person's plugins are in force.
NO_WINDOW_YET = "--no-startup-window"
#: How long the engine is given to bring the person's tabs back into its first window, and
#: how long none may be added before they count as all there.
_REOPEN_WAIT = 4.0
_REOPEN_QUIET = 0.5
_NEW_TAB = ("chrome://newtab", "chrome://new-tab-page")


def on_start_up(profile: Path) -> tuple[bool, list[str]]:
    """What the person chose under "On start-up" in the engine's settings: whether it
    continues where they left off, and the pages of their own it starts on.

    The engine keeps that choice in one of two files, depending on the system; where the
    guarded one holds it, that is the one the engine goes by.
    """
    choice: Any = None
    pages: Any = None
    for name in ("Secure Preferences", "Preferences"):
        try:
            kept = json.loads((profile / "Default" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        session = kept.get("session") if isinstance(kept, dict) else None
        if not isinstance(session, dict):
            continue
        if choice is None:
            choice = session.get("restore_on_startup")
        if pages is None:
            pages = session.get("startup_urls")
    # 1 continues, 4 starts on their pages, 6 does both; 5, the new tab page, is how it comes.
    theirs = [str(url) for url in pages if isinstance(url, str) and url] if isinstance(pages, list) else []
    return choice in (1, 6), theirs if choice in (4, 6) else []


#: Spell-check in a new profile. On macOS the engine uses the system's own checker and
#: fetches nothing, so it stays on. Elsewhere the engine has no dictionary of its own:
#: measured, it downloads one from its maker's server the first time the person types in a
#: page, and switching the check off does not stop that while a language is still chosen
#: for it. Nothing is fetched that the person did not ask for, so there a new profile starts
#: with the check off and no language chosen for it; the person switches it on and picks the
#: language in the browser's settings, and the engine fetches the dictionary then.
_SYSTEM_SPELL_CHECK = sys.platform == "darwin"


def default_preferences() -> dict[str, Any]:
    """What VOOL writes into a new profile. The person can change any of it in the browser.

    The best of what other browsers offer, with the privacy they leave off: the site
    being visited keeps its cookies so sign-ins last, other sites' cookies are blocked,
    a plain unencrypted page warns before it opens, nothing typed in the address bar is
    sent anywhere until Enter, and files land in a folder of their own. The bookmarks bar
    is shown, as people expect of a browser.

    The starting search and the home button are not here. The engine guards those and
    throws away a value another program wrote; they come with the starting profile, which
    the engine makes itself (``start_profile``).
    """
    return {
        "https_only_mode_enabled": True,
        "enable_do_not_track": True,
        "profile": {
            "cookie_controls_mode": 1,              # block other sites' cookies
            "block_third_party_cookies": True,
            "default_content_setting_values": {"notifications": 2, "geolocation": 3},
        },
        "download": {
            "default_directory": str(downloads_dir()),
            "prompt_for_download": False,
            "directory_upgrade": True,
        },
        "savefile": {"default_directory": str(downloads_dir())},
        "search": {"suggest_enabled": False},
        "alternate_error_pages": {"enabled": False},
        "net": {"network_prediction_options": 2},   # no speculative connections
        "safebrowsing": {"enabled": False, "enhanced": False, "scout_reporting_enabled": False},
        "signin": {"allowed": False},
        "privacy_sandbox": {"m1": {"topics_enabled": False, "fledge_enabled": False, "ad_measurement_enabled": False}},
        "browser": {"check_default_browser": False, **({} if _SYSTEM_SPELL_CHECK else {"enable_spellchecking": False})},
        **({} if _SYSTEM_SPELL_CHECK else {"spellcheck": {"dictionaries": []}}),
        # Read by the engine on macOS only; see ``start_page_is_agreed_to``.
        _AGREED_ONCE: False,
        "bookmark_bar": {"show_on_all_tabs": True},
    }


#: On macOS the engine asks, in a bubble on a new tab ("Did you mean to change this
#: page?"), whether a page that stands in for its own new tab page may stay. It keeps the
#: answer with the page as it was put in, and once per profile it takes whatever stands in
#: at that moment as agreed to; this setting records that the moment has been.
_AGREED_ONCE = "ack_existing_ntp_extensions"
_ASKS_ABOUT_THE_START_PAGE = sys.platform == "darwin"


def start_page_is_agreed_to(profile: Path) -> bool:
    """Have the engine take VOOL's start page as agreed to at this start too. True when
    the profile's settings were changed. Runs before the engine starts.

    VOOL puts its start page into the engine afresh at every start, and the engine keeps
    no answer for a page put in afresh. So from the second start on it would ask about
    VOOL's own page each time the person opens a new tab. This says the engine's once per
    profile has not been yet: the engine then takes what stands in when its first tab is
    made, with the start page loaded, as agreed to, and writes that down itself. A page
    the person adds while the browser is open is still asked about then. The setting is
    the engine's own bookkeeping and none of the person's choices; nothing else in the
    file is changed, and a file that cannot be read is left alone.
    """
    target = profile / "Default" / "Preferences"
    try:
        kept = json.loads(target.read_text(encoding="utf-8"))
        mode = target.stat().st_mode & 0o777
    except (OSError, ValueError):
        return False
    if not isinstance(kept, dict) or kept.get(_AGREED_ONCE, False) is False:
        return False
    kept[_AGREED_ONCE] = False
    temp = target.with_suffix(".vool-tmp")
    try:
        temp.write_text(json.dumps(kept, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temp.chmod(mode)
        temp.replace(target)
    except (OSError, ValueError):
        with contextlib.suppress(OSError):
            temp.unlink()
        return False
    return True


def _laid_over(under: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """``over`` laid on ``under``, group by group, so neither side loses a setting the other lacks."""
    merged = dict(under)
    for key, value in over.items():
        merged[key] = _laid_over(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


async def seed_profile(profile: Path) -> bool:
    """Give a profile that has no settings yet its starting ones. True when written.

    It begins as a copy of the starting profile, which holds the settings the engine made
    itself (the starting search, the home button), with VOOL's own settings laid over it.
    Never touches a profile the person has already used: their own choices win.
    """
    from . import start_profile

    target = profile / "Default" / "Preferences"
    if target.exists():
        return False
    binary = engine_binary()
    start = await start_profile.ready(binary) if binary else None
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    made_by_engine: dict[str, Any] = {}
    if start is not None:
        for name in start_profile.KEEP:
            kept = start / "Default" / name
            if not kept.is_file():
                continue
            if name == "Preferences":
                with contextlib.suppress(OSError, ValueError):
                    read = json.loads(kept.read_text(encoding="utf-8"))
                    made_by_engine = read if isinstance(read, dict) else {}
            else:
                shutil.copy2(kept, target.parent / name)
    temp = target.with_suffix(".vool-tmp")
    temp.write_text(json.dumps(_laid_over(made_by_engine, default_preferences())), encoding="utf-8")
    temp.replace(target)
    return True


def _route_flags() -> list[str]:
    """The engine's way out. Raises when Private web is on and cannot carry traffic."""
    from core import private_web

    state = private_web.current_route()
    if not state.active:
        return []
    if private_web.route_problem(state):
        raise DeskFault("private_web_down")
    if not state.uses_socks:
        return []  # the person's own system VPN carries everything; it was just confirmed
    host, port = state.endpoint
    return [
        f"--proxy-server=socks5://{host}:{port}",
        # Names are resolved by the route, never by this machine.
        "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        # The engine does not apply its rule for the maker's update address (see the quiet
        # list) to what it hands to a route, so that one name is kept off the route. Past
        # the route the engine is given no address for any name (the rule above), so the
        # request has nowhere to go and the name is never looked up on this machine.
        f"--proxy-bypass-list={MAKER_UPDATE_HOST}",
    ]


def launch_flags(*, private: bool) -> list[str]:
    # A person is browsing: pages are not told this is an automated session.
    flags = [*UNATTENDED_UI_FLAGS, *QUIET_FLAGS, "--disable-blink-features=AutomationControlled", *_route_flags()]
    # The same for both kinds of window: the engine starts with no window, and VOOL then
    # makes the first one, a private one in the engine's own private side.
    # VOOL loads its own site protection into every window after the start, and, into the
    # lasting window only, the browser plugins the person enabled. A private window gets
    # no plugin.
    from .extensions import LOAD_FLAG

    flags.append(LOAD_FLAG)
    return flags


def _check_available() -> None:
    from . import live

    if not live.available():
        raise DeskFault("web_off")


class _Window:
    """One engine process: the lasting browser, or one private window."""

    def __init__(self, profile: Path, *, private: bool) -> None:
        self.profile = profile
        self.private = private
        self.context: Any = None
        # The engine as started. ``context`` is the same thing, except when the engine
        # would not open its own private window and a side of it that keeps nothing was
        # made in its place.
        self.engine: Any = None
        # The engine's name for its private side, once this window is its private window.
        self.side = ""
        self.driver: Any = None
        self.tmp: Path | None = None
        self.closed = asyncio.Event()
        # The way out this engine was started with. It cannot change while it runs.
        self.route: list[str] = []
        # Tabs VOOL is working in. An address the person opens never lands in one.
        self.reserved: set[Any] = set()
        # Enabled browser plugins loaded into this engine: plugin id -> the engine's id.
        self.extensions: dict[str, str] = {}
        self.extension_folders: dict[str, str] = {}
        # VOOL's site protection as loaded into this engine: the engine's id for it, the
        # folder it was loaded from, and how many listed sites it stops.
        self.guard_id = ""
        self.guard_folder = ""
        self.guard_sites = 0
        # VOOL's start page as loaded into this engine: the engine's id for it.
        self.home_id = ""
        # The empty tab VOOL made at this start, until the person or an address uses it.
        self.fresh: Any = None
        # True from the start until the window is first asked to show something.
        self.just_started = False

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        binary = engine_binary()
        if not binary:
            raise DeskFault("engine_missing")
        resolved = self.profile.resolve()
        if not resolved.is_relative_to(active_data_dir().resolve()) or refuse_real_profile_target(str(resolved)):
            raise DeskFault("invalid_profile")
        flags = launch_flags(private=self.private)
        self.route = _route_flags()
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        await seed_profile(self.profile)
        if _ASKS_ABOUT_THE_START_PAGE:
            start_page_is_agreed_to(self.profile)
        continues, their_pages = on_start_up(self.profile)
        with contextlib.suppress(OSError):
            downloads_dir().mkdir(parents=True, exist_ok=True)
        # The start page is on disk before the engine starts: an engine that had it loaded
        # at its last quit reads the folder again by itself as it starts.
        start_page: Path | None = None
        with contextlib.suppress(Exception):
            from . import home

            start_page = home.build()
        # Chromium's sockets need a short path; see live.py for the Linux launch failure.
        self.tmp = Path(tempfile.mkdtemp(prefix="vool-db-"))
        self.driver = await async_playwright().start()
        try:
            self.context = await self.driver.chromium.launch_persistent_context(
                user_data_dir=str(self.profile), executable_path=binary, headless=False,
                # The complete argument list is this module's own; see NEVER_PASSED.
                # It must name the profile itself: with the handle's defaults off, an
                # engine given no profile would open the person's own browser profile.
                args=[f"--user-data-dir={self.profile}", *flags, *extra_engine_flags,
                      "--remote-debugging-pipe", NO_WINDOW_YET], ignore_default_args=True,
                chromium_sandbox=True, timeout=ENGINE_START_TIMEOUT * 1000,
                env=child_env(TMPDIR=str(self.tmp)), no_viewport=True, accept_downloads=True,
            )
        except Exception:
            await self.stop()
            raise DeskFault("engine_failed") from None
        self.engine = self.context
        self.engine.on("close", lambda _ctx: self.closed.set())
        try:
            # The engine handle would otherwise catch every download into a hidden folder
            # under a made-up name. Downloads here belong to the person: the browser saves
            # them itself, under their real names, in the downloads folder.
            await self._downloads_are_the_engines_own()
            # No window exists yet, so no page has loaded. The lists of harmful sites are
            # put in force first: every tab, a reopened one too, loads under today's lists.
            # A protection that will not load does not keep the browser from opening; the
            # window's state says it is not on.
            with contextlib.suppress(Exception):
                from . import guard

                await guard.apply(self)
            # VOOL's own start page takes the place of the engine's new tab page. A private
            # window keeps the engine's own page for such windows, which is on the computer;
            # the start page is loaded there too, for an ordinary window the person opens
            # from the private one: its new tab would otherwise be whatever page the search
            # the engine holds gives for new tabs, fetched from that search's site.
            with contextlib.suppress(Exception):
                from . import home

                await home.apply(self, start_page)
            if not self.private:
                # A plugin that will not load does not keep the browser from opening.
                with contextlib.suppress(Exception):
                    from . import extensions

                    await extensions.apply(self)
            # The first window. When the person chose to continue where they left off, the
            # engine brings their tabs back into it by itself.
            if self.private:
                await self._private_window()
            else:
                self.fresh = await self.context.new_page()
            if continues:
                await self._reopened_tabs()
            if continues and not their_pages:
                # The engine leaves the empty tab in front. The tab the person used last
                # goes in front, as it was when they closed the browser.
                with contextlib.suppress(Exception):
                    from . import front_tab

                    await front_tab.bring_last_used(self)
            # Pages of their own to start on: the engine opens those only in a window it
            # starts with, so VOOL opens them, the first in the tab just made and in front.
            first_of_theirs: Any = None
            for url in their_pages:
                page = self.fresh or await self._tab()
                self.fresh = None
                first_of_theirs = first_of_theirs or page
                with contextlib.suppress(Exception):
                    await page.goto(url, wait_until="commit")
            if first_of_theirs is not None:
                with contextlib.suppress(Exception):
                    await first_of_theirs.bring_to_front()
            if self.fresh is not None and [page for page in self.context.pages if not page.is_closed() and page is not self.fresh]:
                # With their tabs back, the empty tab VOOL made is not needed.
                await self.fresh.close()
                self.fresh = None
        except Exception:
            await self.stop()
            raise DeskFault("engine_failed") from None
        self.just_started = True
        if self.private:
            # On macOS the engine keeps running after its last window is closed. A
            # private window with no tab left is over: it is stopped and removed then,
            # not whenever the person next quits.
            def watch(page: Any) -> None:
                page.on("close", lambda _page: self._last_tab_closed())

            for side in {self.context, self.engine}:
                side.on("page", watch)
                for page in side.pages:
                    watch(page)

    async def _downloads_are_the_engines_own(self) -> None:
        session = await self.engine.browser.new_browser_cdp_session()
        try:
            for side in ("", *((await session.send("Target.getBrowserContexts")).get("browserContextIds") or [])):
                await session.send("Browser.setDownloadBehavior", {"behavior": "default", **({"browserContextId": side} if side else {})})
        finally:
            with contextlib.suppress(Exception):
                await session.detach()

    async def _private_window(self) -> None:
        """The first window of a private start: the engine's own private window, which
        looks private and whose new tab is the engine's page for such windows, on the
        computer, whichever search the engine holds. Nothing of it is written to the
        profile."""
        from . import private_side

        tab = await private_side.tab(self)
        if tab is not None:
            with contextlib.suppress(Exception):
                self.side = await private_side.side_of(self)
            self.fresh = tab
            return
        # The engine would not open its private window for VOOL's part. A side of the
        # engine that keeps nothing is private all the same; it does not look it, and
        # its new tab is an empty page. Nothing about how pages look is set.
        self.context = await self.engine.browser.new_context(
            no_viewport=True, color_scheme="null", reduced_motion="null", forced_colors="null", contrast="null")
        await self._downloads_are_the_engines_own()
        self.fresh = await self.context.new_page()

    async def _tab(self) -> Any:
        """A new tab in this window. In the engine's private window the engine handle's
        own way to a tab would open an ordinary window, so the engine is asked from
        inside, as for the window itself."""
        if self.private and self.side:
            from . import private_side

            tab = await private_side.tab(self)
            if tab is not None:
                return tab
        return await self.context.new_page()

    async def _reopened_tabs(self) -> None:
        """Wait for the tabs the engine brings back into its first window. They are all
        there once some have come and none was added for a moment; an engine with nothing
        to bring back is not waited for beyond a few seconds."""
        loop = asyncio.get_running_loop()
        end = loop.time() + _REOPEN_WAIT
        count, since = 0, loop.time()
        while loop.time() < end:
            now = len([page for page in self.context.pages if not page.is_closed() and page is not self.fresh])
            if now != count:
                count, since = now, loop.time()
            elif count and loop.time() - since >= _REOPEN_QUIET:
                return
            await asyncio.sleep(0.05)

    def _unused_tab(self) -> Any:
        """A tab of the person's that an address may take: still on the new tab page, or
        the empty one VOOL made at the start. Never an empty-looking tab the engine is
        still reopening."""
        for page in self.context.pages:
            if page.is_closed() or page in self.reserved:
                continue
            if (page is self.fresh and page.url == "about:blank") or self._new_tab(page):
                return page
        return None

    def _new_tab(self, page: Any) -> bool:
        """Still on the new tab page: the engine's own, or VOOL's start page."""
        from . import home

        return page.url.startswith(_NEW_TAB) or page.url == (home.address(self) or None)

    def _last_tab_closed(self) -> None:
        # An ordinary window the person opened from the private one counts as part of it.
        sides = {side for side in (self.context, self.engine) if side is not None}
        if sides and not [p for side in sides for p in side.pages if not p.is_closed()]:
            self.closed.set()

    def workers(self) -> list[Any]:
        """The background workers of what VOOL loaded into the engine. A private window's
        engine has them in its lasting side or its private one."""
        sides = [self.context, *([self.engine] if self.engine is not self.context else [])]
        return [worker for side in sides if side is not None for worker in side.service_workers]

    def alive(self) -> bool:
        return self.context is not None and not self.closed.is_set()

    async def open(self, url: str) -> None:
        # An empty tab of the person's is reused; a tab VOOL is working in never is.
        pages = [p for p in self.context.pages if not p.is_closed() and p not in self.reserved]
        blank = self._unused_tab()
        showing = [p for p in pages if not (p is self.fresh and p.url == "about:blank")]
        just_started, self.just_started = self.just_started, False
        if not url and showing:
            # No address asked for and the window already shows something. A window that
            # has just started is in front already, on the tab the person left in front or
            # the first of the pages they start on. An older one comes forward.
            if not just_started:
                with contextlib.suppress(Exception):
                    await showing[-1].bring_to_front()
            return
        page = blank or await self._tab()
        for _ in range(3):
            try:
                # No address asked for: the window shows its start page, not an empty tab.
                await page.goto(url or HOME, wait_until="commit")
                break
            except Exception:
                # The address is the person's own; a page that fails to load shows the
                # engine's own error page, which is what a browser does. A tab that
                # never left its empty start was cut off before it began: go again.
                if page.is_closed():
                    page = await self._tab()
                elif page.url != "about:blank":
                    break
                else:
                    await asyncio.sleep(0.3)
        with contextlib.suppress(Exception):
            await page.bring_to_front()

    async def stop(self) -> bool:
        """Close the engine and, for a private window, remove every trace. True when clean."""
        context, self.context = self.context, None
        engine, self.engine = self.engine, None
        if context is not None:
            # A tab VOOL was working in is not one of the person's: it is closed first, so
            # a start that continues where they left off does not bring it back as theirs.
            for page in list(self.reserved):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(page.close(), STOP_TIMEOUT)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(context.close(), STOP_TIMEOUT)
        if engine is not None and engine is not context:
            # A private window was a side of the engine: the engine itself goes with it.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(engine.close(), STOP_TIMEOUT)
        driver, self.driver = self.driver, None
        if driver is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(driver.stop(), STOP_TIMEOUT)
        self.closed.set()
        clean = True
        if self.tmp is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None
        if self.private:
            shutil.rmtree(self.profile, ignore_errors=True)
            clean = not self.profile.exists()
        return clean


class Desk:
    """Owns the lasting browser and any private windows for one data folder."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="vool-desk-browser", daemon=True)
        self._thread.start()
        self._main: _Window | None = None
        self._private: list[_Window] = []
        # Held here so a watcher lives as long as its window: a task nothing holds can be dropped.
        self._watchers: set[asyncio.Task[None]] = set()
        self._gate: asyncio.Lock | None = None
        self.unclean_private: list[str] = []
        self._lists_timer: asyncio.TimerHandle | None = None

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coro, timeout: float = ENGINE_START_TIMEOUT + STOP_TIMEOUT + 10):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _locked(self, coro):
        if self._gate is None:
            self._gate = asyncio.Lock()
        async with self._gate:
            return await coro

    # -- person-facing doors -------------------------------------------------

    def open(self, url: str = "", *, private: bool = False) -> dict[str, Any]:
        _check_available()
        address = normalise_address(url)
        self._call(self._locked(self._open(address, private)))
        return self.status()

    async def _open(self, url: str, private: bool) -> None:
        await self._reap()
        await self._close_off_route()
        if private:
            window = _Window(_root() / ("private-" + secrets.token_hex(8)), private=True)
            await window.start()
            self._private.append(window)
            watcher = asyncio.ensure_future(self._watch_private(window))
            self._watchers.add(watcher)
            watcher.add_done_callback(self._watchers.discard)
        else:
            window = await self._ensure_main()
        self._keep_lists_fresh()
        await window.open(url)

    async def _ensure_main(self) -> _Window:
        if self._main is None or not self._main.alive():
            if self._main is not None:
                await self._main.stop()
            self._main = _Window(profile_dir(), private=False)
            try:
                await self._main.start()
            except DeskFault:
                self._main = None
                raise
        return self._main

    async def tab_for_agent(self) -> Any:
        """A new tab in the everyday window for VOOL's own steps. Runs on this desk's loop.

        The window is started when it is not open, on the route in force. Never a
        private window: what the person does there is not VOOL's to read.
        """
        _check_available()

        async def make() -> Any:
            await self._reap()
            await self._close_off_route()
            window = await self._ensure_main()
            self._keep_lists_fresh()
            page = await window.context.new_page()
            window.reserved.add(page)
            page.on("close", lambda closed: window.reserved.discard(closed))
            return page

        return await self._locked(make())

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    async def _watch_private(self, window: _Window) -> None:
        await window.closed.wait()
        await self._locked(self._reap())

    async def _reap(self) -> None:
        """Private windows the person closed: remove their profile now, not at quit."""
        for window in [w for w in self._private if not w.alive()]:
            self._private.remove(window)
            if not await window.stop():
                self.unclean_private.append(str(window.profile))
        # A folder left by an earlier run that ended without cleanup.
        for leftover in _root().glob("private-*") if _root().is_dir() else ():
            if not any(w.profile == leftover for w in self._private):
                shutil.rmtree(leftover, ignore_errors=True)
                if leftover.exists() and str(leftover) not in self.unclean_private:
                    self.unclean_private.append(str(leftover))
        self.unclean_private = [p for p in self.unclean_private if Path(p).exists()]

    async def _close_off_route(self) -> None:
        """Close every window that was started on a way out that is no longer the one in force.

        An engine's route is fixed when it starts. When the person switches Private web
        on, off or to another route, a window left running would keep sending the old
        way, so it is closed; the next open starts on the new route.
        """
        try:
            wanted: list[str] | None = _route_flags()
        except DeskFault:
            wanted = None  # the route is down: nothing may stay open on any other way
        windows = [*self._private, *([self._main] if self._main else [])]
        for window in windows:
            if window.alive() and window.route != wanted:
                if window is self._main:
                    self._main = None
                else:
                    self._private.remove(window)
                if not await window.stop():
                    self.unclean_private.append(str(window.profile))

    def route_changed(self) -> None:
        self._call(self._locked(self._close_off_route()))

    def plugins_changed(self) -> None:
        """A browser plugin was enabled, switched off or removed: an open window follows."""
        from . import extensions

        async def follow() -> None:
            if self._main is not None and self._main.alive():
                await extensions.apply(self._main)

        self._call(self._locked(follow()))

    def _keep_lists_fresh(self) -> None:
        """Ask for today's lists of harmful sites, and look again each hour while a window is open.

        Runs on this desk's loop and never waits: a window opens with the lists already held
        and follows when a newer release has been taken. The source itself is asked at most
        once a day (``site_lists.refresh``).
        """
        with contextlib.suppress(Exception):
            from . import site_lists

            site_lists.refresh_in_background(lambda result: self.lists_changed() if result == "updated" else None)
        if self._lists_timer is None:
            self._lists_timer = self._loop.call_later(LISTS_LOOK_SECONDS, self._look_for_lists)

    def _look_for_lists(self) -> None:
        self._lists_timer = None
        if any(window.alive() for window in [*self._private, *([self._main] if self._main else [])]):
            self._keep_lists_fresh()

    def lists_changed(self) -> None:
        """A new release of the site lists was taken: every open window follows."""
        from . import guard

        async def follow() -> None:
            for window in [*self._private, *([self._main] if self._main else [])]:
                if window.alive():
                    with contextlib.suppress(Exception):
                        await guard.apply(window)

        self._call(self._locked(follow()))

    def stop(self) -> dict[str, Any]:
        self._call(self._locked(self._stop()))
        return self.status()

    async def _stop(self) -> None:
        windows = [*self._private, *([self._main] if self._main else [])]
        self._private, self._main = [], None
        for window in windows:
            if not await window.stop():
                self.unclean_private.append(str(window.profile))
        await self._reap()

    def status(self) -> dict[str, Any]:
        from . import live

        async def read() -> dict[str, Any]:
            await self._reap()
            return {
                "open": bool(self._main and self._main.alive()),
                "plugins": sorted(self._main.extensions) if self._main and self._main.alive() else [],
                "protected": bool(self._main and self._main.alive() and self._main.guard_id),
                "private_windows": len(self._private),
                "private_cleanup_failed": bool(self.unclean_private),
            }

        state = self._call(self._locked(read()))
        from . import site_lists

        return {"ok": True, "available": live.available(), "engine": bool(engine_binary()),
                "site_lists": site_lists.status(), **state}

    def context_for_agent(self) -> Any:
        """The lasting browser's context, for the agent's own steps. None when closed.

        Private windows are never handed out: what the person does there is not the
        agent's to read.
        """
        return self._main.context if self._main and self._main.alive() else None


def normalise_address(value: str) -> str:
    """What the person typed, as an address the engine can open. Empty stays empty."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 4096 or any(ch in text for ch in "\r\n\x00"):
        raise DeskFault("invalid_address")
    lowered = text.lower()
    if "://" not in text:
        if lowered.startswith(("javascript:", "data:", "file:", "chrome:", "about:", "view-source:")):
            raise DeskFault("invalid_address")
        return "https://" + text
    if not lowered.startswith(("http://", "https://")):
        raise DeskFault("invalid_address")
    return text


_DESKS: dict[str, Desk] = {}
_LOCK = threading.Lock()


def desk() -> Desk:
    key = str(active_data_dir())
    with _LOCK:
        item = _DESKS.get(key)
        if item is None:
            item = _DESKS[key] = Desk()
        return item


def route_changed() -> None:
    """Private web was switched: close every window still on the earlier way out."""
    with _LOCK:
        items = list(_DESKS.values())
    for item in items:
        item.route_changed()


def plugins_changed() -> None:
    """The set of enabled browser plugins changed: every open lasting window follows."""
    with _LOCK:
        items = list(_DESKS.values())
    for item in items:
        with contextlib.suppress(Exception):
            item.plugins_changed()


def stop_all() -> None:
    with _LOCK:
        items = list(_DESKS.values())
    for item in items:
        with contextlib.suppress(Exception):
            item.stop()


__all__ = [
    "NEVER_PASSED",
    "QUIET_FLAGS",
    "Desk",
    "DeskFault",
    "default_preferences",
    "desk",
    "downloads_dir",
    "engine_binary",
    "launch_flags",
    "normalise_address",
    "plugins_changed",
    "profile_dir",
    "route_changed",
    "seed_profile",
    "stop_all",
]
