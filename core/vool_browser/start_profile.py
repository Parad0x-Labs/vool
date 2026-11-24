"""The starting profile: the settings a browser engine accepts only from itself.

An engine guards a handful of its settings against other programs: the search in use,
the home button, the pages opened at the start. A value written into a profile's settings
file from outside is thrown away at the next start. Measured on Google Chrome 158 on
macOS: the starting search VOOL had written came back as the engine maker's own, the home
button was off, and the engine asked that company's site for something at every start.
The engine keeps such a setting only when its own settings page made it.

So VOOL has the engine make them, once. The engine is run hidden, with no way out to any
network, on an empty profile. On its own settings page the starting search is added and
made the one in use, and the home button is switched on, by the page's own controls: the
page decides what to tell the engine, as it does for a person. The engine is closed, and
the settings files it wrote are kept as the starting profile. Every new profile begins as
a copy of them: the lasting window's when it is first made, and each private window's.

A starting profile is made for one engine and one starting search. Another engine, or
another search, gets one of its own and the old one is removed. When the engine cannot
make one, new profiles begin with the settings VOOL can write itself and the engine's own
search; the browser still opens.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from core.secret_env import child_env

from .engine import QUIET_FLAGS, UNATTENDED_UI_FLAGS

FORMAT = 1
#: The settings files a starting profile consists of. The engine keeps guarded settings
#: in the second one where the system has it guard them, and searches in the third.
KEEP: tuple[str, ...] = ("Preferences", "Secure Preferences", "Web Data")
#: The run that makes a starting profile sends everything to an address on this machine
#: where nothing listens, this machine included, so nothing it asks for leaves.
NO_WAY_OUT: tuple[str, ...] = ("--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>")
SETTINGS_PAGE = "chrome://settings/searchEngines"
#: How long the settings page is given to show each of its controls.
_PAGE_WAIT_MS = 8000

_PAGE_TOOLS = """
const all = (sel, root = document, out = []) => {
  for (const el of root.querySelectorAll('*')) { if (el.matches(sel)) out.push(el); if (el.shadowRoot) all(sel, el.shadowRoot, out); }
  return out;
};
const until = async (test, ms = WAIT_MS) => {
  const end = Date.now() + ms;
  for (;;) { const got = test(); if (got) return got; if (Date.now() > end) return null; await new Promise((r) => setTimeout(r, 50)); }
};
const entries = () => all('settings-search-engine-entry').map((el) => ({el, engine: el.engine})).filter((e) => e.engine);
const pref = (key) => new Promise((done) => chrome.settingsPrivate.getPref(key, (got) => done(got ? got.value : null)));
"""

#: Adds the search with the page's own "Add" window, makes it the one in use with the
#: entry's own "Make default", and shows the home button. Answers what it could not do.
_SET = "async ([name, keyword, url, WAIT_MS]) => {" + _PAGE_TOOLS + """
  const add = await until(() => all('#addSearchEngine')[0]);
  if (!add) return {problem: 'no_add_button'};
  if (!entries().some((e) => e.engine.url === url)) {
    add.click();
    const dialog = await until(() => all('settings-search-engine-edit-dialog')[0]);
    if (!dialog) return {problem: 'no_add_window'};
    for (const [id, value] of [['searchEngine', name], ['keyword', keyword], ['queryUrl', url]]) {
      const input = await until(() => dialog.shadowRoot.querySelector('#' + id));
      if (!input) return {problem: 'no_field_' + id};
      input.value = value;
      input.dispatchEvent(new CustomEvent('value-changed', {detail: {value}, bubbles: true, composed: true}));
      input.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
    }
    const save = await until(() => { const b = dialog.shadowRoot.querySelector('#actionButton'); return b && !b.disabled ? b : null; });
    if (!save) return {problem: 'search_refused'};
    save.click();
  }
  const mine = await until(() => entries().find((e) => e.engine.url === url));
  if (!mine) return {problem: 'search_not_listed'};
  if (!mine.engine.default) {
    const make = mine.el.shadowRoot.querySelector('#makeDefaultOption') || mine.el.shadowRoot.querySelector('#makeDefault');
    if (!make) return {problem: 'no_make_default'};
    make.click();
  }
  if (!(await until(() => entries().find((e) => e.engine.url === url && e.engine.default)))) return {problem: 'search_not_in_use'};
  await new Promise((done) => chrome.settingsPrivate.setPref('browser.show_home_button', true, '', done));
  if ((await pref('browser.show_home_button')) !== true) return {problem: 'home_button_refused'};
  return {problem: ''};
}"""

#: What the engine itself says is in force, read from its settings page.
READ = "async () => { const WAIT_MS = 8000;" + _PAGE_TOOLS + """
  await until(() => entries().length);
  return {search: entries().filter((e) => e.engine.default).map((e) => [e.engine.name, e.engine.url]),
          home_button: await pref('browser.show_home_button')};
}"""

#: Why the last attempt for an engine and search made no starting profile. One attempt
#: per run of the app: a failed one costs seconds, and nothing changes until a restart.
_failed: dict[str, str] = {}


def search() -> tuple[str, str, str]:
    """The starting search as the settings page takes it: name, shortcut, address with %s."""
    from . import desk

    chosen = desk.STARTING_SEARCH
    return str(chosen["name"]), str(chosen["keyword"]), str(chosen["url"]).replace("{searchTerms}", "%s")


def folder(binary: str) -> Path:
    from . import desk

    made_for = json.dumps([FORMAT, str(binary), list(search()), list(desk.extra_engine_flags)])
    return desk._root() / ("start-" + hashlib.sha256(made_for.encode("utf-8")).hexdigest()[:16])


def problem(binary: str) -> str:
    """Why this engine has no starting profile, or "" when it has one or was not asked yet."""
    return _failed.get(folder(binary).name, "")


def _kept(start: Path) -> dict[str, Any]:
    """The guarded settings as the engine wrote them, from whichever file holds them."""
    found: dict[str, Any] = {}
    for name in ("Preferences", "Secure Preferences"):
        try:
            written = json.loads((start / "Default" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data = (written.get("default_search_provider_data") or {}).get("template_url_data") or {}
        if data.get("url"):
            found["search"] = data["url"]
        shown = (written.get("browser") or {}).get("show_home_button")
        if shown is not None:
            found["home_button"] = shown
    return found


async def _make(binary: str, work: Path) -> str:
    from playwright.async_api import async_playwright

    from . import desk

    tmp = Path(tempfile.mkdtemp(prefix="vool-ds-"))
    driver = await async_playwright().start()
    try:
        context = await driver.chromium.launch_persistent_context(
            user_data_dir=str(work), executable_path=binary, headless=False,
            # The complete argument list is this module's own, as for every window.
            args=[f"--user-data-dir={work}", *UNATTENDED_UI_FLAGS, *QUIET_FLAGS, *desk.extra_engine_flags,
                  "--headless=new", *NO_WAY_OUT, "--remote-debugging-pipe", "about:blank"],
            ignore_default_args=True, chromium_sandbox=True, timeout=desk.ENGINE_START_TIMEOUT * 1000,
            env=child_env(TMPDIR=str(tmp)), no_viewport=True,
        )
        try:
            page = await context.new_page()
            await page.goto(SETTINGS_PAGE)
            done = await page.evaluate(_SET, [*search(), _PAGE_WAIT_MS])
        finally:
            # A clean close is what makes the engine write its settings files.
            await context.close()
    except Exception:
        return "engine_failed"
    finally:
        with contextlib.suppress(Exception):
            await driver.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    if not isinstance(done, dict) or done.get("problem") != "":
        return str((done or {}).get("problem") or "settings_page_failed") if isinstance(done, dict) else "settings_page_failed"
    kept = _kept(work)
    wanted = search()[2].replace("%s", "{searchTerms}")
    if kept.get("search") != wanted or kept.get("home_button") is not True:
        return "not_written"
    return ""


async def ready(binary: str) -> Path | None:
    """The starting profile for this engine, made now if there is none. None when the
    engine could not make one; :func:`problem` then says why."""
    from . import desk

    target = folder(binary)
    if (target / "Default" / "Preferences").is_file():
        return target
    if target.name in _failed:
        return None
    root = desk._root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    work = root / (target.name + ".making")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(mode=0o700)
    try:
        why = await _make(binary, work)
        if why:
            _failed[target.name] = why
            return None
        # Only the settings files are kept: nothing else of that run belongs in a new profile.
        clean = root / (target.name + ".kept")
        shutil.rmtree(clean, ignore_errors=True)
        (clean / "Default").mkdir(parents=True, mode=0o700)
        for name in KEEP:
            written = work / "Default" / name
            if written.is_file():
                shutil.copy2(written, clean / "Default" / name)
        shutil.rmtree(target, ignore_errors=True)
        clean.replace(target)
    except OSError:
        _failed[target.name] = "not_written"
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for older in root.glob("start-*"):
        if older != target:
            shutil.rmtree(older, ignore_errors=True)
    return target


__all__ = ["KEEP", "NO_WAY_OUT", "READ", "SETTINGS_PAGE", "folder", "problem", "ready", "search"]
