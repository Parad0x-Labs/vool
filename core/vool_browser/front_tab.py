"""The tab in front after the engine reopened the person's tabs.

With "Continue where you left off" the engine brings the person's tabs back into its first
window. That window already holds the one empty tab it was made with, and an engine that
reopens tabs into a window with a tab in it leaves that tab in front: which of the
person's tabs was in front when they closed the browser is not carried over. The engine
does know when each reopened tab was last used, and tells only a part that runs inside it.

So for such a start VOOL puts a part of its own into the engine for a moment. It holds one
question and no permission: among the reopened tabs of the first window, bring the one used
last to the front. It cannot read an address, a title or a page, and it is taken out again
before the start is over. When the engine will not take it, the start goes on and the
engine's own choice of tab stays.
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
        "manifest_version": 3, "name": "VOOL: the tab in front", "version": "1.0",
        # No permission and no access to any site: the engine then gives a tab's place
        # and the time it was last used, never its address or title.
        "background": {"service_worker": "front.js"},
    }, indent=2) + "\n",
    "front.js": "// Holds nothing. VOOL asks its one question from outside, once, at the start.\n",
}
#: Asked inside the part. The tab made first is the empty one VOOL's window began with;
#: the reopened tabs of that window are the person's.
_BRING_LAST_USED = """() => new Promise((done) => chrome.tabs.query({}, (tabs) => {
  if (chrome.runtime.lastError || !tabs || !tabs.length) return done(false);
  const first = tabs.reduce((a, b) => (a.id < b.id ? a : b));
  const theirs = tabs.filter((tab) => tab.windowId === first.windowId && tab.id !== first.id && typeof tab.lastAccessed === 'number');
  if (!theirs.length) return done(false);
  const last = theirs.reduce((a, b) => (a.lastAccessed >= b.lastAccessed ? a : b));
  chrome.tabs.update(last.id, {active: true}, () => done(!chrome.runtime.lastError));
}))"""
#: How long the engine is given to start the part.
_WAIT = 3.0


async def bring_last_used(window: Any) -> bool:
    """Bring the reopened tab the person used last to the front of *window*'s first window.

    Runs on the desk loop, once, at a start that reopened tabs, while the empty tab the
    window was made with is still there. True when the engine did it.
    """
    if window.context is None or window.context.browser is None or window.tmp is None:
        return False
    folder = Path(window.tmp) / "front"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(mode=0o700)
    for name, text in _FILES.items():
        (folder / name).write_text(text, encoding="utf-8")
    session = await window.context.browser.new_browser_cdp_session()
    engine_id = ""
    try:
        engine_id = str((await session.send("Extensions.loadUnpacked", {"path": str(folder)})).get("id") or "")
        if not engine_id:
            return False
        prefix = f"chrome-extension://{engine_id}/"
        loop = asyncio.get_running_loop()
        end = loop.time() + _WAIT
        while loop.time() < end:
            worker = next((item for item in window.workers() if item.url.startswith(prefix)), None)
            if worker is not None:
                return await asyncio.wait_for(worker.evaluate(_BRING_LAST_USED), _WAIT) is True
            await asyncio.sleep(0.05)
        return False
    except Exception:
        return False
    finally:
        if engine_id:
            with contextlib.suppress(Exception):
                await session.send("Extensions.uninstall", {"id": engine_id})
        with contextlib.suppress(Exception):
            await session.detach()
        shutil.rmtree(folder, ignore_errors=True)


__all__ = ["bring_last_used"]
