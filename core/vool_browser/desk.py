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

from .engine import QUIET_FLAGS, UNATTENDED_UI_FLAGS, find_engine_binary, refuse_real_profile_target

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


def default_preferences() -> dict[str, Any]:
    """What a new profile starts with. The person can change any of it in the browser.

    The best of what other browsers offer, with the privacy they leave off: the site
    being visited keeps its cookies so sign-ins last, other sites' cookies are blocked,
    a plain unencrypted page warns before it opens, nothing typed in the address bar is
    sent anywhere until Enter, and files land in a folder of their own.
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
        "browser": {"check_default_browser": False},
    }


def seed_preferences(profile: Path) -> bool:
    """Write the starting preferences into a profile that has none. True when written.

    Never touches a profile the person has already used: their own choices win.
    """
    target = profile / "Default" / "Preferences"
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = target.with_suffix(".vool-tmp")
    temp.write_text(json.dumps(default_preferences()), encoding="utf-8")
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
    ]


def launch_flags(*, private: bool) -> list[str]:
    # A person is browsing: pages are not told this is an automated session.
    flags = [*UNATTENDED_UI_FLAGS, *QUIET_FLAGS, "--disable-blink-features=AutomationControlled", *_route_flags()]
    if private:
        flags.append("--incognito")
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
        seed_preferences(self.profile)
        with contextlib.suppress(OSError):
            downloads_dir().mkdir(parents=True, exist_ok=True)
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
                      "--remote-debugging-pipe", "about:blank"], ignore_default_args=True,
                chromium_sandbox=True, timeout=ENGINE_START_TIMEOUT * 1000,
                env=child_env(TMPDIR=str(self.tmp)), no_viewport=True, accept_downloads=True,
            )
        except Exception:
            await self.stop()
            raise DeskFault("engine_failed") from None
        self.context.on("close", lambda _ctx: self.closed.set())
        # The engine handle would otherwise catch every download into a hidden folder
        # under a made-up name. Downloads here belong to the person: the browser saves
        # them itself, under their real names, in the downloads folder.
        try:
            pages = self.context.pages or [await self.context.new_page()]
            session = await self.context.new_cdp_session(pages[0])
            await session.send("Browser.setDownloadBehavior", {"behavior": "default"})
            await session.detach()
            # The engine pins its start-up notices to the tab it starts with. One of them
            # warns about launch options, and names the very ones that keep name lookups
            # on the Private web route and the engine's sign-in service away. It also
            # starts that tab on a load of its own, which can cut off the first address
            # opened in it. So the person's tabs are fresh ones, and the start-up tab is
            # closed as soon as one exists. Before the person has a tab, the lists of
            # harmful sites are put in force. A protection that will not load does not
            # keep the browser from opening; the window's state says it is not on.
            with contextlib.suppress(Exception):
                from . import guard

                await guard.apply(self)
            await self.context.new_page()
            for page in pages:
                if not page.is_closed():
                    await page.close()
        except Exception:
            await self.stop()
            raise DeskFault("engine_failed") from None
        if not self.private:
            # A plugin that will not load does not keep the browser from opening.
            with contextlib.suppress(Exception):
                from . import extensions

                await extensions.apply(self)
        if self.private:
            # On macOS the engine keeps running after its last window is closed. A
            # private window with no tab left is over: it is stopped and removed then,
            # not whenever the person next quits.
            def watch(page: Any) -> None:
                page.on("close", lambda _page: self._last_tab_closed())

            self.context.on("page", watch)
            for page in self.context.pages:
                watch(page)

    def _last_tab_closed(self) -> None:
        context = self.context
        if context is not None and not [p for p in context.pages if not p.is_closed()]:
            self.closed.set()

    def alive(self) -> bool:
        return self.context is not None and not self.closed.is_set()

    async def open(self, url: str) -> None:
        # An empty tab of the person's is reused; a tab VOOL is working in never is.
        pages = [p for p in self.context.pages if not p.is_closed() and p not in self.reserved]
        blank = next((p for p in pages if p.url == "about:blank"), None)
        page = blank or await self.context.new_page()
        for _ in range(3 if url else 0):
            try:
                await page.goto(url, wait_until="commit")
                break
            except Exception:
                # The address is the person's own; a page that fails to load shows the
                # engine's own error page, which is what a browser does. A tab that
                # never left its empty start was cut off before it began: go again.
                if page.is_closed():
                    page = await self.context.new_page()
                elif page.url != "about:blank":
                    break
                else:
                    await asyncio.sleep(0.3)
        with contextlib.suppress(Exception):
            await page.bring_to_front()

    async def stop(self) -> bool:
        """Close the engine and, for a private window, remove every trace. True when clean."""
        context, self.context = self.context, None
        if context is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(context.close(), STOP_TIMEOUT)
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
            asyncio.ensure_future(self._watch_private(window))
        else:
            window = await self._ensure_main()
        self._keep_lists_fresh()
        await window.open(url)

    async def _ensure_main(self) -> "_Window":
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
    "NEVER_PASSED", "QUIET_FLAGS", "Desk", "DeskFault", "desk", "engine_binary",
    "default_preferences", "downloads_dir", "launch_flags", "normalise_address", "profile_dir",
    "plugins_changed", "route_changed", "seed_preferences", "stop_all",
]
