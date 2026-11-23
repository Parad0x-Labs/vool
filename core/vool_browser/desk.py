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

from .engine import UNATTENDED_UI_FLAGS, find_engine_binary, refuse_real_profile_target

ENGINE_START_TIMEOUT = 30.0
STOP_TIMEOUT = 15.0

#: Playwright's automation defaults that have no place in a browser a person uses
#: every day. Each is removed for a stated reason; the guard test reads this tuple.
DROPPED_AUTOMATION_DEFAULTS: tuple[str, ...] = (
    "--enable-automation",          # the "controlled by automated software" bar
    "--disable-extensions",         # extensions are a feature here
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--password-store=basic",       # would keep saved sign-ins in a store any program can read
    "--use-mock-keychain",          # same, on macOS
    "--disable-popup-blocking",     # the popup blocker stays on
    "--no-sandbox",
    "--mute-audio",
    "--hide-scrollbars",
)

#: Quiet by default: the engine's own phone-home services stay off. Page requests
#: the person makes are the only traffic.
QUIET_FLAGS: tuple[str, ...] = (
    "--disable-sync",
    "--disable-background-networking",
    "--disable-breakpad",
    "--disable-domain-reliability",
    "--no-pings",
    "--no-service-autorun",
    "--metrics-recording-only",
)


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

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        binary = engine_binary()
        if not binary:
            raise DeskFault("engine_missing")
        resolved = self.profile.resolve()
        if not resolved.is_relative_to(active_data_dir().resolve()) or refuse_real_profile_target(str(resolved)):
            raise DeskFault("invalid_profile")
        flags = launch_flags(private=self.private)
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Chromium's sockets need a short path; see live.py for the Linux launch failure.
        self.tmp = Path(tempfile.mkdtemp(prefix="vool-db-"))
        self.driver = await async_playwright().start()
        try:
            self.context = await self.driver.chromium.launch_persistent_context(
                user_data_dir=str(self.profile), executable_path=binary, headless=False,
                args=flags, ignore_default_args=list(DROPPED_AUTOMATION_DEFAULTS),
                chromium_sandbox=True, timeout=ENGINE_START_TIMEOUT * 1000,
                env=child_env(TMPDIR=str(self.tmp)), no_viewport=True, accept_downloads=True,
            )
        except Exception:
            await self.stop()
            raise DeskFault("engine_failed") from None
        self.context.on("close", lambda _ctx: self.closed.set())
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
        pages = [p for p in self.context.pages if not p.is_closed()]
        blank = next((p for p in pages if p.url in ("about:blank", "chrome://newtab/", "chrome://new-tab-page/")), None)
        page = blank or await self.context.new_page()
        if url:
            # The address is the person's own; a page that fails to load shows the
            # engine's own error page, which is what a browser does.
            with contextlib.suppress(Exception):
                await page.goto(url, wait_until="commit")
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
        if private:
            window = _Window(_root() / ("private-" + secrets.token_hex(8)), private=True)
            await window.start()
            self._private.append(window)
            asyncio.ensure_future(self._watch_private(window))
        else:
            if self._main is None or not self._main.alive():
                if self._main is not None:
                    await self._main.stop()
                self._main = _Window(profile_dir(), private=False)
                try:
                    await self._main.start()
                except DeskFault:
                    self._main = None
                    raise
            window = self._main
        await window.open(url)

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
                "private_windows": len(self._private),
                "private_cleanup_failed": bool(self.unclean_private),
            }

        state = self._call(self._locked(read()))
        return {"ok": True, "available": live.available(), "engine": bool(engine_binary()), **state}

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


def stop_all() -> None:
    with _LOCK:
        items = list(_DESKS.values())
    for item in items:
        with contextlib.suppress(Exception):
            item.stop()


__all__ = [
    "DROPPED_AUTOMATION_DEFAULTS", "QUIET_FLAGS", "Desk", "DeskFault", "desk", "engine_binary",
    "launch_flags", "normalise_address", "profile_dir", "stop_all",
]
