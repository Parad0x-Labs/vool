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
- behind the words is a picture of the hour: a print of land under the sun or the moon,
  with VOOL's flag on it and one line about VOOL beneath. It is drawn on the page from
  the clock and the date. Nothing is fetched for it, and it holds nothing of the person
  (``picture``, which the Browser panel inside VOOL draws from too);
- a sun and a moon in its corner keep the page light or dark whatever the hour; pressed a
  second time, the same one gives the page back to the clock. Which was pressed is kept in
  the page's own storage in the profile and goes nowhere;
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

from .picture import PICTURE_JS

#: Raised whenever the files below change, so an older copy is never loaded.
FORMAT = 5
PAGE = "home.html"

#: The folder the page is loaded from. It is always the same one: the engine names what
#: it loads by the folder it came from, and it remembers by that name what the person
#: answered when it asked whether this page may stand in for its own new tab page.
FOLDER = "page"

_WORDS = ("home_name", "home_title", "home_search", "home_visited",
          "home_welcome", "home_welcome_note", "home_morning", "home_afternoon", "home_evening",
          "home_point_memory", "home_point_cost", "home_point_local", "home_point_account",
          "home_point_ask", "home_point_forget")

_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title></title><link rel="stylesheet" href="home.css"></head>
<body><svg id="sky" aria-hidden="true"></svg><main>
<h1>VOOL</h1>
<p id="hello" hidden><strong id="helloTitle"></strong><span id="helloNote"></span></p>
<form id="search" role="search" autocomplete="off"><input id="q" type="text" autofocus spellcheck="false"></form>
<section id="visited" hidden><h2 id="visitedTitle"></h2><div id="sites"></div></section>
<section id="picks" hidden></section>
<div id="look" role="group">
<button id="lookLight" type="button" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.6M12 18.9v2.6M2.5 12h2.6M18.9 12h2.6M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/></svg></button>
<button id="lookDark" type="button" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.5 14.6A8 8 0 0 1 9.4 4.5a8 8 0 1 0 10.1 10.1z"/></svg></button>
</div>
</main><p id="point"></p><script src="home.js"></script></body></html>
"""

_CSS = """:root{color-scheme:light;--paper:#eee8d5;--ink:#1a1a1a;--quiet:rgba(26,26,26,.68);--line:rgba(26,26,26,.6);--field:#f3eedd}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;flex-direction:column;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;overflow-x:hidden}
#sky{position:fixed;inset:0;width:100%;height:100%;z-index:0}
[dir=rtl] #sky{transform:scaleX(-1)}
main{position:relative;z-index:1;width:min(38rem,100% - 3rem);margin:0;margin-inline-start:max(1.5rem,7vw);padding:12vh 0 2rem;text-align:start}
h1{font-size:2.6rem;letter-spacing:.42em;font-weight:600;margin:0 0 1.4rem}
#q{width:100%;font:inherit;font-size:1.05rem;padding:.9rem 1.15rem;border-radius:.2rem;border:1.5px solid var(--line);background:var(--field);color:var(--ink);box-shadow:5px 5px 0 var(--line)}
#q:focus-visible{outline:2px solid var(--ink);outline-offset:3px}
#q::placeholder{color:var(--quiet)}
#hello{margin:-.4rem 0 1.5rem;color:var(--quiet)}
#hello strong{display:block;color:var(--ink);font:italic 500 1.55rem/1.3 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;margin-bottom:.1rem}
h2{font-size:.85rem;font-weight:600;letter-spacing:.06em;color:var(--quiet);margin:2.2rem 0 .8rem}
#sites{display:flex;flex-wrap:wrap;gap:.55rem}
#sites a{display:grid;grid-template-columns:auto minmax(0,1fr);column-gap:.6rem;align-items:center;padding:.4rem .8rem;border:1.5px solid var(--line);border-radius:.2rem;background:var(--field);color:var(--ink);text-decoration:none;max-width:12rem}
#sites a:hover{box-shadow:3px 3px 0 var(--line)}
#sites a:focus-visible{outline:2px solid var(--ink);outline-offset:3px}
#sites img{grid-row:span 2;width:20px;height:20px;border-radius:3px}
#sites span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.88rem;line-height:1.25}
#sites small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--quiet);font-size:.72rem;line-height:1.25}
#point{position:relative;z-index:1;margin:auto 0 0;padding:1rem 1.5rem 2rem;padding-inline-start:max(1.5rem,7vw);max-width:38rem;color:#eee8d5;font:italic 500 1.9rem/1.2 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;text-shadow:0 1px 2px rgba(0,0,0,.6)}
#point::before{content:"";display:block;width:3.2rem;border-top:2px solid #eee8d5;margin-bottom:.7rem}
#look{position:fixed;bottom:2rem;inset-inline-end:1.5rem;display:flex;gap:.45rem;--on:#eee8d5;--under:#1a1a1a;--rim:rgba(238,232,213,.6)}
#look button{display:grid;place-items:center;width:2.4rem;height:2.4rem;padding:0;border:1.5px solid var(--rim);border-radius:.2rem;background:var(--under);color:var(--on);cursor:pointer}
#look button:hover{box-shadow:3px 3px 0 var(--rim)}
#look button:focus-visible{outline:2px solid var(--on);outline-offset:3px}
#look button[aria-pressed=true]{background:var(--on);border-color:var(--on);color:var(--under)}
#look svg{width:1.25rem;height:1.25rem;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
@media (max-width:56rem),(max-height:36rem){main{margin:1.5rem;margin-inline-start:max(1.5rem,7vw);padding:1.5rem;background:var(--paper);border:1.5px solid var(--line);box-shadow:5px 5px 0 var(--line)}h1{font-size:2rem;margin-bottom:1.1rem}#look{position:absolute;top:1.5rem;bottom:auto;--on:var(--ink);--under:var(--field);--rim:var(--line)}}
@media (max-width:56rem){main{margin-inline:auto}#point{align-self:center;padding-inline:1.5rem;text-align:center}#point::before{margin-inline:auto}}
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
""" + PICTURE_JS + """// The page takes the print's own paper and ink, so the words can be read on it at any hour.
function scene(now) {
  const {look, at} = paint($('sky'), now), root = document.documentElement;
  root.dataset.sky = look.name; dress(root.style, look);
  $('point').textContent = WORDS[POINTS[(now.getDate() + at) % POINTS.length]];
}
// The sun and the moon in the corner. One press shows the page by day or by night whatever
// the hour; a second press on the same one gives the page back to the clock. What was
// pressed is kept in this page's own storage in the profile, like the day last greeted.
const LOOKS = [['lookLight', 'light', WORDS.look_light], ['lookDark', 'dark', WORDS.look_dark]];
function kept() { try { const look = localStorage.getItem('look'); return look === 'light' || look === 'dark' ? look : ''; } catch (e) { return ''; } }
let picked = kept();
// The moment the picture is drawn for: the hour it is, unless the look asked for is the
// other one. Then it is noon, or an hour before midnight, of the same day.
function moment(now) {
  if (!picked) return now;
  const hour = now.getHours() + now.getMinutes() / 60, dark = PLATES[PLATES.findLastIndex((one) => hour >= one.from)].dark;
  return dark === (picked === 'dark') ? now : new Date(now.getFullYear(), now.getMonth(), now.getDate(), picked === 'dark' ? 23 : 12);
}
function show(now) {
  scene(moment(now || new Date()));
  for (const [id, look] of LOOKS) $(id).setAttribute('aria-pressed', String(picked === look));
}
for (const [id, look, word] of LOOKS) {
  $(id).title = word; $(id).setAttribute('aria-label', word);
  $(id).addEventListener('click', () => {
    picked = picked === look ? '' : look;
    try { if (picked) localStorage.setItem('look', picked); else localStorage.removeItem('look'); } catch (e) {}
    show();
  });
}
// Another tab of this page was pressed: this one follows.
addEventListener('storage', (event) => { if (event.key === 'look') { picked = kept(); show(); } });
show();
// A page left open keeps up with the day.
setInterval(() => show(), 5 * 60 * 1000);
"""

_RTL = frozenset({"ar", "he", "fa", "ur"})


def _root() -> Path:
    return active_data_dir() / "browser" / "desk" / "home"


def _files(locale: str) -> dict[str, str]:
    from core.i18n import catalog_for

    words_of = catalog_for(locale)
    words = {key: words_of.text("live." + key) for key in _WORDS}
    # The two looks carry the names they have in Settings.
    words["look_light"] = words_of.text("settings.appearance.theme_light")
    words["look_dark"] = words_of.text("settings.appearance.theme_dark")
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
