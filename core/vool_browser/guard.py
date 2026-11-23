"""Site protection in VOOL's browser windows.

The lists of harmful sites (``site_lists``) are handed to the engine as its own built-in
kind of block rules, inside a small extension VOOL writes and loads itself. The engine then
stops a listed site before any request for it leaves: a page on a listed site is replaced
by VOOL's warning, and anything a page tries to load from a listed site is dropped.

Rules kept here:

- it is VOOL's own part of the browser, not a plugin: it is loaded in the lasting window
  and in every private window, and it is never offered for review or switched per plugin;
- a window's tabs are opened only after the engine itself reports the rules in force;
- the person can open a listed site anyway from the warning, with one press. That choice
  covers that one site, lasts until the browser is closed, and is the person's alone:
  VOOL's own steps never open a listed site (``live`` and ``desk_session`` refuse it first);
- nothing here goes online. The extension asks for no page content and holds no page script.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from core.runtime_paths import active_data_dir

#: Raised whenever the files below change, so an older copy is never loaded.
FORMAT = 1

#: How long a start waits for the engine to report the rules in force.
ARM_SECONDS = 15.0

_WORDS = ("guard_name", "guard_title", "guard_text", "guard_back", "guard_open", "guard_open_note")

_RESOURCE_TYPES = ["main_frame", "sub_frame", "stylesheet", "script", "image", "font", "object",
                   "xmlhttprequest", "ping", "csp_report", "media", "websocket", "webtransport",
                   "webbundle", "other"]

_WORKER = """// Gives the warning page the address that was stopped, and lets the person open one
// listed site anyway until the browser is closed. No page content is read here.
const TYPES = __TYPES__;
const PAGE = chrome.runtime.getURL('blocked.html');
async function arm() {
  const rules = await (await fetch(chrome.runtime.getURL('rules.json'))).json();
  await chrome.declarativeNetRequest.updateSessionRules({removeRuleIds: [10], addRules: [{id: 10, priority: 2,
    action: {type: 'redirect', redirect: {regexSubstitution: PAGE + '#\\\\0'}},
    condition: {regexFilter: '^https?://.*', requestDomains: rules[0].condition.requestDomains, resourceTypes: ['main_frame']}}]});
}
chrome.runtime.onInstalled.addListener(arm);
chrome.runtime.onStartup.addListener(arm);
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  const host = String((message && message.host) || '');
  if (!message || message.what !== 'open' || sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0
      || !String(sender.url || '').startsWith(PAGE) || !/^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$/.test(host)) return;
  (async () => {
    const held = await chrome.declarativeNetRequest.getSessionRules();
    const id = Math.max(100, ...held.map((rule) => rule.id)) + 1;
    await chrome.declarativeNetRequest.updateSessionRules({addRules: [{id, priority: 3, action: {type: 'allow'},
      condition: {requestDomains: [host], resourceTypes: TYPES}}]});
    reply({ok: true});
  })();
  return true;
});
"""

_BLOCKED_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title></title><link rel="stylesheet" href="blocked.css"></head>
<body><main>
<h1 id="title"></h1>
<p id="host"></p>
<p id="text"></p>
<div class="row"><button id="back" class="primary" type="button"></button><button id="open" type="button" hidden></button></div>
<p id="note" class="quiet" hidden></p>
</main><script src="blocked.js"></script></body></html>
"""

_BLOCKED_CSS = """:root{color-scheme:light dark;--bg:#f6f5f2;--ink:#1d1c1a;--quiet:#66625c;--line:#d9d5cd;--accent:#1d1c1a;--on:#f6f5f2}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--ink:#ece9e3;--quiet:#a19c93;--line:#34322e;--accent:#ece9e3;--on:#141413}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:34rem;padding:2rem}
h1{font-size:1.5rem;line-height:1.25;margin:0 0 1rem}
#host{font-weight:600;overflow-wrap:anywhere;margin:0 0 .5rem}
p{margin:0 0 1.25rem}
.quiet{color:var(--quiet);font-size:.9rem;margin-top:1.25rem}
.row{display:flex;gap:.75rem;flex-wrap:wrap}
button{font:inherit;padding:.6rem 1.1rem;border-radius:.6rem;border:1px solid var(--line);background:transparent;color:var(--ink);cursor:pointer}
button.primary{background:var(--accent);color:var(--on);border-color:var(--accent)}
button:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
"""

_BLOCKED_JS = """const WORDS = __WORDS__;
const DIRECTION = __DIRECTION__;
let target = '', host = '';
try {
  const url = new URL(decodeURIComponent(location.hash.slice(1)));
  if (url.protocol === 'http:' || url.protocol === 'https:') { target = url.href; host = url.hostname; }
} catch (e) {}
const $ = (id) => document.getElementById(id);
document.documentElement.lang = WORDS.lang; document.documentElement.dir = DIRECTION;
document.title = WORDS.guard_title;
$('title').textContent = WORDS.guard_title; $('text').textContent = WORDS.guard_text;
$('host').textContent = host; $('back').textContent = WORDS.guard_back;
$('open').textContent = WORDS.guard_open; $('note').textContent = WORDS.guard_open_note;
// The choice to open a listed site is offered only on the warning itself, never inside another page.
const whole = window.top === window;
$('open').hidden = $('note').hidden = !(whole && host);
$('back').onclick = () => { if (history.length > 1) history.back(); else location.replace('about:blank'); };
$('open').onclick = async () => {
  $('open').disabled = true;
  try { const answer = await chrome.runtime.sendMessage({what: 'open', host}); if (answer && answer.ok) { location.replace(target); return; } } catch (e) {}
  $('open').disabled = false;
};
"""

#: Asked of the engine inside the protection's own worker: would a page on this listed site be
#: stopped by the list right now? Nothing goes online for the answer.
_PROBE = """async (host) => {
  const stops = async (type) => (await chrome.declarativeNetRequest.testMatchOutcome({url: 'http://' + host + '/', type})).matchedRules || [];
  // A part of a page is stopped by the list itself; the page is stopped by the list or by the
  // rule that also hands the warning its address.
  return (await stops('image')).some((rule) => rule.rulesetId === 'list') && (await stops('main_frame')).length > 0;
}"""

_RTL = frozenset({"ar", "he", "fa", "ur"})


def _root() -> Path:
    return active_data_dir() / "browser" / "desk" / "guard"


def _language_path() -> Path:
    return active_data_dir() / "browser" / "desk" / "language"


def remember_language(locale: str) -> None:
    """The language the person last saw VOOL's browser pane in; the warning page uses it."""
    tag = str(locale or "").strip()
    if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", tag):
        return
    path = _language_path()
    with contextlib.suppress(OSError):
        if path.is_file() and path.read_text(encoding="ascii").strip() == tag:
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(tag + "\n", encoding="ascii")


def language() -> str:
    try:
        return _language_path().read_text(encoding="ascii").strip() or "en"
    except (OSError, UnicodeError):
        return "en"


def _files(hosts: list[str], locale: str) -> dict[str, str]:
    from core.i18n import catalog_for

    words_of = catalog_for(locale)
    words = {key: words_of.text("live." + key) for key in _WORDS}
    words["lang"] = locale
    direction = "rtl" if locale.split("-")[0].lower() in _RTL else "ltr"
    manifest = {
        "manifest_version": 3, "name": words["guard_name"], "version": f"{FORMAT}.0",
        # A private window gets its own running copy, so the warning page opens there too.
        "incognito": "split",
        "permissions": ["declarativeNetRequest", "declarativeNetRequestFeedback"],
        # Needed to replace a listed page with the warning. No page script is declared and
        # none is ever injected, so no page content is read.
        "host_permissions": ["<all_urls>"],
        "background": {"service_worker": "guard.js"},
        "declarative_net_request": {"rule_resources": [{"id": "list", "enabled": True, "path": "rules.json"}]},
        "web_accessible_resources": [{"resources": ["blocked.html"], "matches": ["<all_urls>"]}],
    }
    rules = [
        {"id": 1, "priority": 1, "action": {"type": "redirect", "redirect": {"extensionPath": "/blocked.html"}},
         "condition": {"requestDomains": hosts, "resourceTypes": ["main_frame"]}},
        # With no types named, this covers everything a page loads except the page itself.
        {"id": 2, "priority": 1, "action": {"type": "block"}, "condition": {"requestDomains": hosts}},
    ]
    script = lambda value: json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")  # noqa: E731
    return {
        "manifest.json": json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        "rules.json": json.dumps(rules, separators=(",", ":")) + "\n",
        "guard.js": _WORKER.replace("__TYPES__", json.dumps(_RESOURCE_TYPES)),
        "blocked.html": _BLOCKED_HTML,
        "blocked.css": _BLOCKED_CSS,
        "blocked.js": _BLOCKED_JS.replace("__WORDS__", script(words)).replace("__DIRECTION__", script(direction)),
    }


def build(hosts: frozenset[str], locale: str = "", *, loaded: frozenset[str] = frozenset()) -> Path | None:
    """Write the protection for *hosts* and return its folder, or None when there is nothing to block.

    The folder is named by its contents and written afresh unless the engine has it loaded
    right now (*loaded*, by folder name), so what is loaded is what VOOL wrote, even if the
    folder was changed while the browser was closed. Folders of earlier lists are removed.
    """
    base = _root()
    ordered = sorted(hosts)
    target: Path | None = None
    if ordered:
        files = _files(ordered, locale or language())
        digest = hashlib.sha256()
        for name in sorted(files):
            digest.update(name.encode() + b"\0" + files[name].encode("utf-8") + b"\0")
        name = "guard-" + digest.hexdigest()[:16]
        target = base / name
        if not (name in loaded and target.is_dir()):
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            staging = base / (name + ".new")
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(target, ignore_errors=True)
            staging.mkdir(mode=0o700)
            for filename, text in files.items():
                path = staging / filename
                path.write_text(text, encoding="utf-8")
                path.chmod(0o600)
            staging.rename(target)
    if base.is_dir():
        keep = {target.name if target else "", *loaded}
        for path in base.iterdir():
            if path.name not in keep:
                shutil.rmtree(path, ignore_errors=True)
    return target


async def apply(window: Any) -> bool:
    """Put the lists in force in *window*. Runs on the desk loop. True when they are in force
    (or there is nothing to block).

    At a start the caller has not opened the person's tabs yet, and this returns only after
    the engine reports the rules in force. When the lists change while a window is open, the
    new protection is loaded and confirmed before the earlier one is let go, so there is no
    moment without one.
    """
    from . import site_lists

    if window.context is None or window.context.browser is None:
        return False
    hosts = site_lists.hosts()
    held_id, held_folder = str(getattr(window, "guard_id", "") or ""), str(getattr(window, "guard_folder", "") or "")
    folder = build(hosts, loaded=frozenset({held_folder} - {""}))
    if folder is not None and folder.name == held_folder:
        return True
    session = await window.context.browser.new_browser_cdp_session()
    try:
        if folder is None:
            if held_id:
                with contextlib.suppress(Exception):
                    await session.send("Extensions.uninstall", {"id": held_id})
            window.guard_id = window.guard_folder = ""
            window.guard_sites = 0
            return True
        new_id = ""
        try:
            new_id = await _load(session, folder)
            armed = bool(new_id) and await _armed(window, new_id, min(hosts))
        except Exception:
            armed = False
        if not armed:
            # The earlier protection, when there is one, stays in force.
            if new_id:
                with contextlib.suppress(Exception):
                    await session.send("Extensions.uninstall", {"id": new_id})
            return False
        if held_id:
            with contextlib.suppress(Exception):
                await session.send("Extensions.uninstall", {"id": held_id})
        window.guard_id, window.guard_folder, window.guard_sites = new_id, folder.name, len(hosts)
        return True
    finally:
        with contextlib.suppress(Exception):
            await session.detach()


async def _load(session: Any, folder: Path) -> str:
    """Load the protection into the engine, for its private windows too. Returns the engine's id."""
    try:
        answer = await session.send("Extensions.loadUnpacked", {"path": str(folder), "enableInIncognito": True})
    except Exception:
        # An engine that does not know the private-window setting still protects its ordinary windows.
        answer = await session.send("Extensions.loadUnpacked", {"path": str(folder)})
    return str(answer.get("id") or "")


async def _armed(window: Any, engine_id: str, host: str) -> bool:
    """Wait until the engine itself says a page on a listed site would be stopped."""
    prefix = f"chrome-extension://{engine_id}/"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ARM_SECONDS
    while loop.time() < deadline:
        worker = next((item for item in window.context.service_workers if item.url.startswith(prefix)), None)
        if worker is not None:
            with contextlib.suppress(Exception):
                if await worker.evaluate(_PROBE, host) is True:
                    return True
        await asyncio.sleep(0.05)
    return False


__all__ = ["apply", "build", "language", "remember_language"]
