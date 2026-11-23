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
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from . import desk as desk_module
from . import live
from .live import TIMEOUT, BrowserFault, LiveSession


#: A human check, found by its shape and never by a page's wording: a visible frame served
#: by a service whose only job is to tell people from programs, or a visible element carrying
#: the standard widget marker those services require. Fixed source; no page text is used.
_HUMAN_CHECK = """() => {
  const hosts = ['recaptcha.net', 'hcaptcha.com', 'challenges.cloudflare.com', 'arkoselabs.com',
                 'funcaptcha.com', 'geetest.com', 'captcha-delivery.com', 'perimeterx.net', 'px-captcha'];
  const shown = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width >= 60 && r.height >= 40 && s.visibility !== 'hidden' && s.display !== 'none'; };
  for (const frame of document.querySelectorAll('iframe[src]')) {
    let url; try { url = new URL(frame.src, location.href); } catch (e) { continue; }
    const host = url.hostname;
    const service = hosts.some((h) => host === h || host.endsWith('.' + h))
      || ((host === 'google.com' || host.endsWith('.google.com')) && url.pathname.startsWith('/recaptcha/'));
    if (service && shown(frame)) return true;
  }
  for (const el of document.querySelectorAll('[data-sitekey], .g-recaptcha, .h-captcha, .cf-turnstile')) {
    if (shown(el)) return true;
  }
  return false;
}"""

#: Said to the model by VOOL itself, never taken from a page.
HUMAN_CHECK_NOTE = (
    "This site is asking the person to confirm they are human. Do not try to pass, solve or "
    "work around that check. Tell the person it is waiting in the VOOL browser tab, and after "
    "they say it is done, call vool-browser.inspect to read the page again."
)


# VOOL's tab is closed when no step has used it for this long.
IDLE_SECONDS = 1800


class DeskSession(LiveSession):
    in_window = True

    def _check_owner_lease(self) -> None:
        # The separate view lives only while the person keeps its pane open. This tab is
        # in the person's own window, in plain sight, and each step still waits for their
        # answer, so a closed pane does not end it in the middle of a task. It ends when
        # nothing has used it for a while.
        if self.closed:
            return
        if time.monotonic() < max(self._owner_deadline, self.last_activity + IDLE_SECONDS):
            self._loop.call_later(min(5.0, IDLE_SECONDS / 2), self._check_owner_lease)
            return
        self._lease_cleanup = threading.Thread(target=self._expire_owner_lease, daemon=True)
        self._lease_cleanup.start()

    def show(self) -> bool:
        """Bring VOOL's tab to the front of the person's window. Only the person asks for this."""
        page = self._page
        if self.closed or page is None or page.is_closed():
            return False
        try:
            asyncio.run_coroutine_threadsafe(page.bring_to_front(), self._loop).result(timeout=5)
        except Exception:
            return False
        return True

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
        # The person is usually looking at another tab while VOOL works in this one, and
        # the engine draws nothing for a tab that is not in front: a picture of it then
        # never arrives. A capture kept open on this tab makes the engine go on drawing
        # it, without bringing it to the front. It asks for a tiny frame, rarely; the
        # pictures the pane shows are taken separately, at full size.
        self._drawing = session
        session.on("Page.screencastFrame", lambda frame: asyncio.ensure_future(self._frame_seen(session, frame)))
        await session.send("Page.startScreencast", {"format": "jpeg", "quality": 1, "maxWidth": 32,
                                                    "maxHeight": 32, "everyNthFrame": 60})

    @staticmethod
    async def _frame_seen(session: Any, frame: dict) -> None:
        with contextlib.suppress(Exception):
            await session.send("Page.screencastFrameAck", {"sessionId": frame["sessionId"]})

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
        self._observed_origin = self._document_origin
        if await self._page.evaluate(_HUMAN_CHECK) is True:
            self.result["human_check"] = {"required": True, "note": HUMAN_CHECK_NOTE}
        else:
            self.result.pop("human_check", None)

    def refresh(self) -> str:
        """Read VOOL's tab again as it is now, without sending anything. Returns what happened.

        A live page changes by itself, and the person may have acted in the tab (a human
        check, a consent choice). Reading is refused when the tab has left the site VOOL was
        allowed onto: where the person went by hand is not VOOL's to read.
        """
        with self._mutex:
            page = self._page
            if self.closed or self.state != "ready" or self.pending or page is None or page.is_closed():
                return "unchanged"
            self.last_activity = time.monotonic()

        async def read() -> str:
            parts = urlsplit(page.url)
            if (parts.scheme, parts.netloc) != getattr(self, "_observed_origin", None):
                return "moved"
            await self._observe()
            return "read"

        try:
            return asyncio.run_coroutine_threadsafe(read(), self._loop).result(timeout=TIMEOUT + 4)
        except Exception:
            return "unchanged"

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
