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
When the engine will not do it, the caller falls back to what the engine does from outside.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
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
#: How long the engine is given to start the part, and to show the window's first tab.
_WAIT = 3.0


async def side_of(window: Any) -> str:
    """The engine's name for its private side once *window* has a private window, or ""."""
    session = await window.engine.browser.new_browser_cdp_session()
    try:
        sides = await session.send("Target.getBrowserContexts")
        lasting = str(sides.get("defaultBrowserContextId") or "")
        made_from_outside = set(sides.get("browserContextIds") or [])
        for target in (await session.send("Target.getTargets")).get("targetInfos") or []:
            side = str(target.get("browserContextId") or "")
            if target.get("type") == "page" and lasting and side and side != lasting and side not in made_from_outside:
                return side
        return ""
    finally:
        with contextlib.suppress(Exception):
            await session.detach()


async def tab(window: Any) -> Any:
    """A tab in the engine's private window of *window*, which is opened when it has none.
    Runs on the desk loop. Returns the tab, or None when the engine did not make it."""
    if window.engine is None or window.engine.browser is None or window.tmp is None:
        return None
    folder = Path(window.tmp) / "private"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(mode=0o700)
    for name, text in _FILES.items():
        (folder / name).write_text(text, encoding="utf-8")
    before = set(window.engine.pages)
    session = await window.engine.browser.new_browser_cdp_session()
    engine_id = ""
    try:
        engine_id = str((await session.send("Extensions.loadUnpacked", {"path": str(folder), "enableInIncognito": True})).get("id") or "")
        if not engine_id:
            return None
        prefix = f"chrome-extension://{engine_id}/"
        loop = asyncio.get_running_loop()
        end = loop.time() + _WAIT
        asked = False
        while loop.time() < end + (_WAIT if asked else 0):
            if not asked:
                worker = next((item for item in window.engine.service_workers if item.url.startswith(prefix)), None)
                if worker is not None:
                    if await asyncio.wait_for(worker.evaluate(_OPEN), _WAIT) is not True:
                        return None
                    asked = True
            else:
                tab = next((page for page in window.engine.pages if page not in before and not page.is_closed()), None)
                if tab is not None:
                    return tab
            await asyncio.sleep(0.05)
        return None
    except Exception:
        return None
    finally:
        if engine_id:
            with contextlib.suppress(Exception):
                await session.send("Extensions.uninstall", {"id": engine_id})
        with contextlib.suppress(Exception):
            await session.detach()
        shutil.rmtree(folder, ignore_errors=True)


__all__ = ["side_of", "tab"]
