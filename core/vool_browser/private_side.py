"""The engine's own private window.

A private window is the engine's own kind: it looks private, says so on its new tab page,
which is on the computer, and keeps nothing. The engine is started without a window (see
``desk``), and a window the engine handle asks for from outside is an ordinary one, or one
in a side of the engine that keeps nothing but looks like any other window and opens an
empty page for a new tab. The engine opens its real private window, and a tab in it, only
for a part that runs inside it.

So VOOL puts a part of its own into the engine for a moment. It holds one request and no
permission: a tab in the private window, which is opened when there is none. It cannot
read an address, a title or a page, and it is taken out again as soon as the tab is there.

What comes back is a tab the engine itself says is in its private side, or nothing: never
a tab of another side that happened to open at the same moment. When the engine will not
do it, the caller is told at which step, and decides what a person gets instead.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

_FILES = {
    "manifest.json": json.dumps({
        "manifest_version": 3, "name": "VOOL: a private window", "version": "1.0",
        # No permission and no access to any site. It may act in private windows, which
        # is what opening one takes.
        "incognito": "spanning",
        "background": {"service_worker": "open.js"},
    }, indent=2) + "\n",
    "open.js": "// Holds nothing. VOOL asks for the window from outside, once, at the start.\n",
}
_OPEN = """() => new Promise((done) => chrome.windows.getAll({windowTypes: ['normal']}, (open) => {
  const made = (it) => done(!chrome.runtime.lastError && !!it && it.incognito === true);
  const there = (open || []).find((one) => one.incognito === true);
  if (there) chrome.tabs.create({windowId: there.id}, made);
  else chrome.windows.create({incognito: true}, made);
}))"""
#: How long the engine is given for each step: to start the part, to answer it, and to
#: show the tab it says it made. A step ends as soon as it is done, so this is only ever
#: spent on an engine that has stalled.
_WAIT = 10.0


class NotMade(Exception):
    """The engine did not make the tab. The text names the step, for whoever reads a
    failure; it is never shown to a person."""


async def _side(window: Any, page: Any) -> str | None:
    """The engine's name for the side *page* is in, or None while it cannot be asked."""
    try:
        session = await window.engine.new_cdp_session(page)
    except Exception:
        return None
    try:
        told = (await session.send("Target.getTargetInfo")).get("targetInfo") or {}
        return str(told.get("browserContextId") or "")
    except Exception:
        return None
    finally:
        with contextlib.suppress(Exception):
            await session.detach()


async def tab(window: Any) -> tuple[Any, str]:
    """A tab in the engine's private window of *window*, which is opened when it has
    none, and the engine's name for the private side the tab is in. Runs on the desk
    loop. Raises NotMade when the engine did not make it."""
    if window.engine is None or window.engine.browser is None or window.tmp is None:
        raise NotMade("the window has no engine")
    folder: Path | None = None
    session: Any = None
    engine_id = ""
    step = "write the part"
    try:
        # A folder of its own for every request. The engine names a part after its
        # folder, so nothing it still holds of an earlier request, a worker that is
        # ending or data it is still clearing, can be taken for this one.
        folder = Path(tempfile.mkdtemp(prefix="private-", dir=window.tmp))
        for name, text in _FILES.items():
            (folder / name).write_text(text, encoding="utf-8")
        before = set(window.engine.pages)
        step = "reach the engine"
        session = await window.engine.browser.new_browser_cdp_session()
        sides = await session.send("Target.getBrowserContexts")
        lasting = str(sides.get("defaultBrowserContextId") or "")
        made_from_outside = {str(side) for side in sides.get("browserContextIds") or []}
        step = "load the part"
        engine_id = str((await session.send("Extensions.loadUnpacked", {"path": str(folder), "enableInIncognito": True})).get("id") or "")
        if not engine_id:
            raise LookupError
        prefix = f"chrome-extension://{engine_id}/"
        loop = asyncio.get_running_loop()
        step = "start the part"
        end, worker = loop.time() + _WAIT, None
        while worker is None:
            worker = next((item for item in window.engine.service_workers if item.url.startswith(prefix)), None)
            if worker is None:
                if loop.time() >= end:
                    raise TimeoutError
                await asyncio.sleep(0.05)
        step = "have the engine open the tab"
        if await asyncio.wait_for(worker.evaluate(_OPEN), _WAIT) is not True:
            raise LookupError
        # The tab is there; the engine handle only has to show it. It is known by the
        # side the engine says it is in: a tab that is new but in the lasting side, or in
        # a side made from outside, is somebody else's, opened at the same moment.
        step = "find the tab the engine opened"
        end, others = loop.time() + _WAIT, set()
        while loop.time() < end:
            for page in window.engine.pages:
                if page in before or page in others or page.is_closed():
                    continue
                side = await _side(window, page)
                if side is None:
                    continue
                if side and lasting and side != lasting and side not in made_from_outside:
                    return page, side
                others.add(page)
            await asyncio.sleep(0.05)
        raise TimeoutError
    except Exception as exc:
        # The step and the kind of failure only: an engine's own message can hold a path.
        raise NotMade(f"could not {step} ({type(exc).__name__})") from None
    finally:
        if engine_id:
            with contextlib.suppress(Exception):
                await session.send("Extensions.uninstall", {"id": engine_id})
        if session is not None:
            with contextlib.suppress(Exception):
                await session.detach()
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)



__all__ = ["NotMade", "tab"]
