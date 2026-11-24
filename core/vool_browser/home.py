"""The start page of VOOL's browser window.

A new tab in the lasting window opens VOOL's own page instead of the engine's: a search
box and the person's most visited sites, in VOOL's look and language. It is a small
extension VOOL writes and loads itself, the way the engine lets a new tab page be replaced.

Rules kept here:

- it is VOOL's own part of the browser, not a plugin, and it is never offered for review;
- nothing here goes online. The search box hands the words to the engine, which uses the
  search the person chose in the browser's settings; an address typed there is opened as
  typed. The most visited sites and their icons come from the engine's own history;
- it asks for no page content and holds no page script, and it cannot see other tabs;
- it welcomes the person the first time it is ever shown and greets them the first time
  it is shown on a day. Which day it last greeted is kept in the page's own storage in
  the profile and goes nowhere;
- a place is kept on it for the top skills and plugins from VOOL's libraries. It is
  empty and not shown: nothing fills it yet;
- a private window keeps the engine's own page, which says that the window is private.
  An ordinary window the person opens from a private one gets this page.
"""
from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path
from typing import Any

from core.runtime_paths import active_data_dir

#: Raised whenever the files below change, so an older copy is never loaded.
FORMAT = 3
PAGE = "home.html"

#: The folder the page is loaded from. It is always the same one: the engine names what
#: it loads by the folder it came from, and it remembers by that name what the person
#: answered when it asked whether this page may stand in for its own new tab page.
FOLDER = "page"

_WORDS = ("home_name", "home_title", "home_search", "home_visited",
          "home_welcome", "home_welcome_note", "home_morning", "home_afternoon", "home_evening")

_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title></title><link rel="stylesheet" href="home.css"></head>
<body><main>
<h1>VOOL</h1>
<p id="hello" hidden><strong id="helloTitle"></strong><span id="helloNote"></span></p>
<form id="search" role="search" autocomplete="off"><input id="q" type="text" autofocus spellcheck="false"></form>
<section id="visited" hidden><h2 id="visitedTitle"></h2><div id="sites"></div></section>
<section id="picks" hidden></section>
</main><script src="home.js"></script></body></html>
"""

_CSS = """:root{color-scheme:light dark;--bg:#f6f5f2;--ink:#1d1c1a;--quiet:#66625c;--line:#d9d5cd;--field:#ffffff}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--ink:#ece9e3;--quiet:#a19c93;--line:#34322e;--field:#1d1c1a}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:44rem;margin:0 auto;padding:18vh 1.5rem 3rem;text-align:center}
h1{font-size:2.25rem;letter-spacing:.35em;font-weight:600;margin:0 0 2rem;padding-inline-start:.35em}
#q{width:100%;font:inherit;font-size:1.05rem;padding:.85rem 1.1rem;border-radius:.8rem;border:1px solid var(--line);background:var(--field);color:var(--ink)}
#q:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
#q::placeholder{color:var(--quiet)}
#hello{margin:-.75rem 0 1.75rem;color:var(--quiet)}
#hello strong{display:block;color:var(--ink);font-size:1.2rem;font-weight:500;margin-bottom:.15rem}
h2{font-size:.85rem;font-weight:500;color:var(--quiet);margin:3rem 0 1rem}
#sites{display:grid;grid-template-columns:repeat(auto-fill,minmax(8.5rem,1fr));gap:.75rem}
#sites a{display:flex;flex-direction:column;align-items:center;gap:.5rem;padding:1rem .75rem;border:1px solid var(--line);border-radius:.8rem;color:var(--ink);text-decoration:none;min-width:0}
#sites a:hover{border-color:var(--quiet)}
#sites a:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
#sites img{width:32px;height:32px;border-radius:6px}
#sites span{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.9rem}
#sites small{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--quiet);font-size:.78rem}
"""

_JS = """const WORDS = __WORDS__;
const DIRECTION = __DIRECTION__;
const $ = (id) => document.getElementById(id);
document.documentElement.lang = WORDS.lang; document.documentElement.dir = DIRECTION;
document.title = WORDS.home_title;
$('q').placeholder = WORDS.home_search; $('q').setAttribute('aria-label', WORDS.home_search);
$('visitedTitle').textContent = WORDS.home_visited;
// The box is ready to type in the moment the page shows. "autofocus" alone waits for the
// engine's next paint, which a busy or covered window can put off: CI on 94b9dad5d
// (2026-11-24) read the page with nothing focused.
$('q').focus();
// A welcome the first time this page is ever shown in this profile, and a greeting the
// first time it is shown on a day, with that day's date. The day last greeted is kept in
// this page's own storage in the profile; nothing about it is sent anywhere.
function greeting(hour) { return hour >= 5 && hour < 12 ? 'home_morning' : hour >= 12 && hour < 18 ? 'home_afternoon' : 'home_evening'; }
function hello(now) {
  const day = [now.getFullYear(), now.getMonth() + 1, now.getDate()].join('-');
  let last = null;
  try { last = JSON.parse(localStorage.getItem('hello') || 'null'); } catch (e) {}
  if (last && last.day === day) return;
  $('helloTitle').textContent = last ? WORDS[greeting(now.getHours())] : WORDS.home_welcome;
  $('helloNote').textContent = last ? now.toLocaleDateString(WORDS.lang, {weekday: 'long', day: 'numeric', month: 'long'}) : WORDS.home_welcome_note;
  $('hello').hidden = false;
  try { localStorage.setItem('hello', JSON.stringify({day})); } catch (e) {}
}
hello(new Date());
// An address is told from words to search for by its shape: it has no spaces, and it is
// a full web address, a name whose last part after a dot is made of letters, or a number.
function address(text) {
  if (/\\s/.test(text)) return '';
  try {
    const whole = new URL(text);
    if (whole.protocol === 'http:' || whole.protocol === 'https:') return whole.href;
  } catch (e) {}
  // This machine, or a machine named by its number: such places are usually plain ones.
  if (/^(localhost|\\d{1,3}(\\.\\d{1,3}){3})(:\\d+)?([/?#].*)?$/i.test(text)) {
    try { return new URL('http://' + text).href; } catch (e) {}
  }
  if (/^[^/?#@:]+\\.\\p{L}{2,}(:\\d+)?([/?#].*)?$/u.test(text)) {
    try { return new URL('https://' + text).href; } catch (e) {}
  }
  return '';
}
$('search').addEventListener('submit', (event) => {
  event.preventDefault();
  const text = $('q').value.trim();
  if (!text) return;
  const to = address(text);
  // Words go to the engine, which searches with the search the person chose in its settings.
  if (to) location.assign(to); else chrome.search.query({text, disposition: 'CURRENT_TAB'});
});
// The sites the person went to most in the last three months, read from the engine's
// history each time this page opens. The engine also keeps a ready-made list of top
// sites, but it brings that one up to date on a timer of its own: a site visited before
// the last quit could be missing from it after a start. One entry per site, the site
// with the most visits first. Its link is the page of that site the person opened most;
// a page address with a question part is never the one linked, so no search words show.
chrome.history.search({text: '', startTime: Date.now() - 90 * 864e5, maxResults: 2000}, (pages) => {
  const sites = new Map();
  for (const page of pages || []) {
    let at;
    try { at = new URL(page.url); } catch (e) { continue; }
    if (at.protocol !== 'http:' && at.protocol !== 'https:') continue;
    const site = sites.get(at.host) || {visits: 0, last: 0, url: at.origin + '/', title: '', most: -1};
    const visits = page.visitCount || 0;
    site.visits += visits; site.last = Math.max(site.last, page.lastVisitTime || 0);
    if (!at.search && visits > site.most) { site.most = visits; site.url = page.url; site.title = page.title || ''; }
    sites.set(at.host, site);
  }
  const shown = [...sites.values()].sort((a, b) => b.visits - a.visits || b.last - a.last).slice(0, 8);
  for (const site of shown) {
    const link = document.createElement('a'), icon = new Image(), name = document.createElement('span'), host = document.createElement('small');
    link.href = site.url;
    // The icon is the one the engine already holds for the page; nothing is fetched for it.
    icon.src = chrome.runtime.getURL('_favicon/') + '?pageUrl=' + encodeURIComponent(site.url) + '&size=32';
    icon.alt = '';
    name.textContent = site.title || new URL(site.url).hostname; host.textContent = new URL(site.url).hostname;
    link.append(icon, name, host);
    $('sites').append(link);
  }
  $('visited').hidden = !shown.length;
});
"""

_RTL = frozenset({"ar", "he", "fa", "ur"})


def _root() -> Path:
    return active_data_dir() / "browser" / "desk" / "home"


def _files(locale: str) -> dict[str, str]:
    from core.i18n import catalog_for

    words_of = catalog_for(locale)
    words = {key: words_of.text("live." + key) for key in _WORDS}
    words["lang"] = locale
    direction = "rtl" if locale.split("-")[0].lower() in _RTL else "ltr"
    manifest = {
        "manifest_version": 3, "name": words["home_name"], "version": f"{FORMAT}.0",
        "chrome_url_overrides": {"newtab": PAGE},
        # The engine's own history, its search, and the icons it already holds. No access
        # to any site, no page script, no background part.
        "permissions": ["history", "search", "favicon"],
    }
    script = lambda value: json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")  # noqa: E731
    return {
        "manifest.json": json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        PAGE: _HTML,
        "home.css": _CSS,
        "home.js": _JS.replace("__WORDS__", script(words)).replace("__DIRECTION__", script(direction)),
    }


def _as_written(folder: Path, files: dict[str, str]) -> bool:
    """True when *folder* holds exactly *files* and nothing else."""
    try:
        if sorted(path.name for path in folder.iterdir()) != sorted(files):
            return False
        return all((folder / name).is_file() and (folder / name).read_text(encoding="utf-8") == text for name, text in files.items())
    except (OSError, ValueError):
        return False


def build(locale: str = "") -> Path:
    """Write the start page in the language VOOL was last seen in and return its folder.

    It is checked against what VOOL writes at every start and written afresh when it
    differs in any way, so what is loaded is what VOOL wrote, even if the folder was
    changed while the browser was closed. A folder that is as written is left alone: an
    open window may be reading it.
    """
    from . import guard

    base = _root()
    files = _files(locale or guard.language())
    target = base / FOLDER
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not _as_written(target, files):
        staging = base / (FOLDER + ".new")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(mode=0o700)
        for filename, text in files.items():
            path = staging / filename
            path.write_text(text, encoding="utf-8")
            path.chmod(0o600)
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
    for path in base.iterdir():
        if path.name != target.name:
            shutil.rmtree(path, ignore_errors=True)
    return target


async def apply(window: Any, folder: Path | None) -> bool:
    """Make VOOL's page, written to *folder*, the new tab page of *window*. Runs on the
    desk loop, once, at its start.

    True when the engine took it. A start page that will not load does not keep the browser
    from opening: new tabs then show the engine's own page.
    """
    if folder is None or window.context is None or window.context.browser is None:
        return False
    session = await window.context.browser.new_browser_cdp_session()
    try:
        answer = await session.send("Extensions.loadUnpacked", {"path": str(folder)})
        engine_id = str(answer.get("id") or "")
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            await session.detach()
    if not engine_id:
        return False
    window.home_id = engine_id
    return True


def address(window: Any) -> str:
    """The address VOOL's start page has in *window*, or "" when it is not loaded there."""
    engine_id = str(getattr(window, "home_id", "") or "")
    return f"chrome-extension://{engine_id}/{PAGE}" if engine_id else ""


__all__ = ["PAGE", "address", "apply", "build"]
