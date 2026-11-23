"""VOOL's own browser steps, run in a tab of the person's VOOL browser window.

The supervised session (``live.LiveSession``) keeps every rule about what VOOL may
do: each step is a one-use proposal the person allows or refuses, only a control
VOOL was shown can be acted on, a form that sends is reviewed field by field,
payment forms go to the wallet, and password, card and one-time-code values are
never read. This class changes only *where* the step runs: a real tab with page
scripts, in the window the person also uses, so VOOL can work on the sites a
person can work on, signed in where the person is signed in.

What this tab may not do, whatever a page asks:

- reach an address on this computer or the local network;
- save a file by itself (a download stays its own reviewed step);
- open further windows;
- outlive the session: the tab is closed when the session ends, and nothing else
  in the window is touched.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from urllib.parse import urlsplit

from . import desk as desk_module
from . import live
from .live import TIMEOUT, BrowserFault, LiveSession


class DeskSession(LiveSession):
    in_window = True

    def _start_loop(self):
        # Engine objects belong to the loop that made them: the desk's.
        self._desk = desk_module.desk()
        return self._desk.loop, None

    def _end_loop(self) -> None:
        # The loop is the desk's and keeps running for the window.
        return None

    async def _open(self) -> None:
        page = self._page
        if page is not None and page.is_closed():
            # The person closed the tab or the window, or Private web was switched and
            # the window was closed with it. The next step gets a fresh tab.
            self._page = self._context = self._opening = None
        await super()._open()

    async def _initialize(self) -> None:
        if self._page is not None and not self._page.is_closed():
            return
        try:
            page = await self._desk.tab_for_agent()
        except desk_module.DeskFault as exc:
            raise BrowserFault(str(exc)) from None
        if self.closed:
            with contextlib.suppress(Exception):
                await page.close()
            raise BrowserFault("stopped")
        self._page = page
        self._context = page.context
        page.set_default_timeout(TIMEOUT * 1000)
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
        page.on("popup", lambda popup: asyncio.ensure_future(popup.close()))
        await page.route("**/*", self._guard_request)
        # A click on a download link must not save a file: a download is its own
        # reviewed step. The setting covers this tab only.
        session = await self._context.new_cdp_session(page)
        await session.send("Page.setDownloadBehavior", {"behavior": "deny"})

    async def _guard_request(self, route: Any) -> None:
        request = route.request
        url = request.url
        if url.startswith(("http://", "https://", "ws://", "wss://")) and live._private_by_name(url, self.fixture_origin):
            self.page_request_refusals += 1
            if request.is_navigation_request() and request.frame == self._page.main_frame:
                self._blocked = "destination_refused"
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _observe_once(self) -> None:
        await super()._observe_once()
        parts = urlsplit(self._page.url)
        self._document_origin = (parts.scheme, parts.netloc)

    async def _close(self) -> None:
        task = self._execution_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        opening = self._opening
        if opening and not opening.done():
            opening.cancel()
            await asyncio.gather(opening, return_exceptions=True)
        page = self._page
        if page is not None and not page.is_closed():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(page.close(), 5)
        self.cleanup_metrics["phase"] = "closed"

    def engine_ready(self) -> bool:
        return self._page is not None and not self._page.is_closed()


__all__ = ["DeskSession"]
