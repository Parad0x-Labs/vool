"""Browser plugins in the everyday window.

A browser plugin is a VOOL plugin whose package carries an ordinary browser extension
(the open format the major browsers share, version 3). It is found, reviewed, installed,
enabled and removed through the same plugin manager as every other VOOL plugin; nothing
here installs or approves anything. This module answers one question, "which extensions
may run right now?", and puts exactly those into the lasting browser window.

Rules kept here:

- only a plugin that is installed, still matches the copy the person approved, and is
  enabled gets loaded;
- the engine writes its own index files into an extension's folder, so it is given a
  working copy under VOOL's browser data, never the approved package itself;
- a private window never gets a plugin;
- the engine keeps nothing between starts: every start loads the current set afresh, so a
  plugin that was switched off or removed while the browser was closed is simply not there.
"""
from __future__ import annotations

import contextlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

#: The engine option that lets its owner load an extension from a folder.
LOAD_FLAG = "--enable-unsafe-extension-debugging"


def enabled() -> list[dict[str, Any]]:
    """Every browser plugin that may run now, with the folder of its approved extension."""
    from core import plugin_lifecycle as lifecycle
    from core.plugin_manifest import browser_extension
    from core.plugin_tools import load_manifest

    found: list[dict[str, Any]] = []
    for record in lifecycle.records():
        if record.stage == lifecycle.STAGE_UNINSTALLED:
            continue
        try:
            if not lifecycle.is_available(record.plugin_id):
                continue
            root = Path(record.root)
            plugin = load_manifest(root / ".codex-plugin" / "plugin.json")
            block = (plugin.vool or {}).get("browser")
            if not block:
                continue
            facts = browser_extension(block, root)
        except Exception:
            # A package that cannot be read or no longer passes its checks does not run.
            continue
        found.append({"id": record.plugin_id, "source": root / facts["folder"],
                      "digest": str(record.verified_digest or ""), **facts})
    return sorted(found, key=lambda row: row["id"])


def _working_root() -> Path:
    from . import desk

    return desk._root() / "extensions"


def _folder_name(row: dict[str, Any]) -> str:
    if not re.fullmatch(r"[a-f0-9]{16,128}", row["digest"]):
        return ""
    return f"{row['id']}-{row['digest'][:16]}"


def working_copies(rows: list[dict[str, Any]], *, loaded: frozenset[str] = frozenset()) -> dict[str, Path]:
    """One writable copy per enabled plugin, named by the approved package it came from.

    A copy the engine has loaded right now (*loaded*, by folder name) is left alone. Every
    other copy is made again from the approved package, so what the engine loads is what the
    person approved even if a working copy was changed while the browser was closed. Copies
    of plugins that are no longer enabled, or of an older approved version, are removed.
    Returns plugin id -> folder to load.
    """
    base = _working_root()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    wanted: dict[str, Path] = {}
    for row in rows:
        name = _folder_name(row)
        if not name:
            continue
        target = base / name
        if not (name in loaded and target.is_dir()):
            shutil.rmtree(target, ignore_errors=True)
            staging = base / (name + ".new")
            shutil.rmtree(staging, ignore_errors=True)
            try:
                # Plain files only: the package was captured without links, and a copy keeps none.
                shutil.copytree(row["source"], staging, symlinks=False)
                for path in [staging, *staging.rglob("*")]:
                    path.chmod(0o700 if path.is_dir() else 0o600)
                staging.rename(target)
            except OSError:
                # This one plugin is left out; the others still load.
                shutil.rmtree(staging, ignore_errors=True)
                continue
        wanted[row["id"]] = target
    keep = {path.name for path in wanted.values()}
    for path in base.iterdir():
        if path.name not in keep:
            shutil.rmtree(path, ignore_errors=True)
    return wanted


async def apply(window: Any) -> dict[str, str]:
    """Make the window's loaded extensions equal the enabled browser plugins. Runs on the desk loop.

    Returns plugin id -> the engine's id for it. A plugin that fails to load is left out;
    the others still load.
    """
    if window.private or window.context is None or window.context.browser is None:
        return {}
    loaded: dict[str, str] = dict(getattr(window, "extensions", {}) or {})
    folders: dict[str, str] = dict(getattr(window, "extension_folders", {}) or {})
    rows = enabled()
    names = {row["id"]: _folder_name(row) for row in rows}
    session = await window.context.browser.new_browser_cdp_session()
    try:
        # Out first: a plugin that is no longer enabled, or whose approved package changed.
        # The engine lets go of the folder before the working copy is removed.
        for plugin_id in [key for key in loaded if names.get(key) != folders.get(key)]:
            with contextlib.suppress(Exception):
                await session.send("Extensions.uninstall", {"id": loaded[plugin_id]})
            loaded.pop(plugin_id, None)
            folders.pop(plugin_id, None)
        copies = working_copies(rows, loaded=frozenset(folders.values()))
        for plugin_id, folder in copies.items():
            if plugin_id in loaded:
                continue
            try:
                answer = await session.send("Extensions.loadUnpacked", {"path": str(folder)})
            except Exception:
                continue
            loaded[plugin_id] = str(answer.get("id") or "")
            folders[plugin_id] = folder.name
    finally:
        with contextlib.suppress(Exception):
            await session.detach()
    window.extensions = loaded
    window.extension_folders = folders
    return loaded


# -- making one ---------------------------------------------------------------------------

_STARTER_SCRIPT = """// This runs on the pages listed under "matches" in manifest.json, after each page has loaded.
// Change it to do what you want. This starter hides anything a page marks as an advert.
for (const part of document.querySelectorAll('[class*="advert"], [id*="advert"]')) {
  part.style.display = 'none';
}
"""

_STARTER_README = """{name}
{line}

A browser plugin for the VOOL browser, made on this computer.

What is in this folder
- .codex-plugin/plugin.json  what VOOL reads: the plugin's name and that it is a browser plugin
- extension/manifest.json    what the browser reads: which pages the plugin works on and what it may do
- extension/page.js          the plugin itself: a script that runs on those pages

How to change it
1. Edit extension/page.js. You can also ask VOOL in chat to write it for you: say what the
   plugin should do and give it this folder.
2. In extension/manifest.json, "matches" lists the pages it runs on. The starter runs on
   every page. To run on one site only, use for example "https://example.org/*".
3. Each time you change the version, change it in both json files (they must match).

How to add it to VOOL
1. Open Add-ons, then Plugins.
2. Under "Install from a folder", give this folder and press "Review folder".
3. Read what it asks for, install it, then enable it.
4. Open the VOOL browser window. A plugin is not loaded in a private window.

What a browser plugin may not do
It may not read saved sign-ins, change the browser's connection, control other plugins,
start programs outside the browser, or save files by itself. VOOL refuses a plugin that asks.
"""


def starter_id(name: str) -> str:
    """A plugin id made from a person's name for it: lower case letters, digits and dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")[:48].strip("-")
    if not slug:
        # A name in letters the id cannot carry keeps its name on the page and gets a plain id.
        return "my-browser-plugin"
    return slug if slug[0].isalpha() else "my-" + slug


def write_starter(folder: Path, name: str) -> Path:
    """Write a complete, installable browser plugin the person can change. Never overwrites:
    a second plugin of the same name gets a number after its id."""
    title = re.sub(r"\s+", " ", str(name or "")).strip()[:80] or "My browser plugin"
    first = starter_id(title)
    plugin_id, number = first, 1
    while (Path(folder) / plugin_id).exists():
        number += 1
        plugin_id = f"{first}-{number}"
    target = Path(folder) / plugin_id
    (target / ".codex-plugin").mkdir(parents=True)
    (target / "extension").mkdir()
    description = "A browser plugin made on this computer."
    (target / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": plugin_id, "version": "0.1.0",
        "interface": {"displayName": title, "shortDescription": description, "category": "Web browser"},
        "vool": {"id": plugin_id, "version": "0.1.0", "name": title, "publisher": "Me",
                 "description": description, "homepage": "https://example.org", "license": "MIT",
                 "kind": ["browser"], "network": [], "credentials": {},
                 "browser": {"extension": "extension"}},
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (target / "extension" / "manifest.json").write_text(json.dumps({
        "manifest_version": 3, "name": title, "version": "0.1.0", "description": description,
        "content_scripts": [{"matches": ["<all_urls>"], "js": ["page.js"], "run_at": "document_idle"}],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (target / "extension" / "page.js").write_text(_STARTER_SCRIPT, encoding="utf-8")
    (target / "README.txt").write_text(_STARTER_README.format(name=title, line="=" * len(title)), encoding="utf-8")
    return target


__all__ = ["LOAD_FLAG", "apply", "enabled", "starter_id", "working_copies", "write_starter"]
