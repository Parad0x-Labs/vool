#!/usr/bin/env python3
"""Check a plugin folder the way VOOL's install door reads it, and say what to change.

    python -m tools.plugin_check FOLDER [--json]

Nothing is installed, started or sent anywhere: the folder is captured into a throwaway profile,
its identity and its setup file are read by the same rules the Add-ons page uses, and the copy is
removed. On a refusal it prints the sentence a person installing the plugin would read, then the
note for the plugin's maker: the field at fault and what to write instead.

Exit codes: 0 the folder would be offered for review; 1 it is refused; 2 the folder could not be read.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check(folder: str) -> dict:
    """{"ok": True, "plugin": {...}} or {"ok": False, "message": ..., "where": ..., "fix": ...}."""
    path = Path(folder).expanduser()
    if not path.is_dir():
        return {"ok": False, "code": 2, "message": "That folder could not be read.", "where": "", "fix": "Give the plugin's folder: the one that holds .codex-plugin/plugin.json."}
    kept = {name: os.environ.get(name) for name in ("VOOL_HOME", "VOOL_PLUGIN_LIFECYCLE_PATH", "VOOL_PLUGINS_DIR", "VOOL_PLUGIN_SCRATCH_ROOT")}
    profile = Path(tempfile.mkdtemp(prefix="vool-plugin-check-"))
    try:
        os.environ.update(VOOL_HOME=str(profile / "home"), VOOL_PLUGIN_LIFECYCLE_PATH=str(profile / "lifecycle.json"),
                          VOOL_PLUGINS_DIR=str(profile / "installed"), VOOL_PLUGIN_SCRATCH_ROOT=str(profile / "scratch"))
        from core import plugin_lifecycle, plugin_manager

        try:
            # The same door the Add-ons page posts to, so the answer is the page's answer.
            review = plugin_manager.handle("plugin_prepare", {"action": "plugin_prepare", "path": str(path), "update": False})
        except plugin_lifecycle.LifecycleError as exc:
            note = getattr(exc, "maker_note", None) or {}
            return {"ok": False, "code": 1, "message": plugin_manager.user_message(str(exc))["message"],
                    "where": note.get("where", ""), "fix": note.get("fix", "")}
        plugin = review["plugin"]
        with contextlib.suppress(Exception):
            plugin_manager.handle("plugin_cancel_review", {"action": "plugin_cancel_review", "review_id": review["review_id"]})
        return {"ok": True, "code": 0, "plugin": {key: plugin.get(key) for key in ("id", "version", "name", "publisher", "kind", "network")},
                "tools": [tool["intent"] for tool in plugin.get("tools", [])], "files": len(review.get("files", []))}
    finally:
        for name, value in kept.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        # VOOL seals what it captures; give the write bits back to this run's own folder before removing it.
        for directory, folders, files in os.walk(profile):
            for name in (*folders, *files):
                with contextlib.suppress(OSError):
                    target = Path(directory) / name
                    if not target.is_symlink():
                        target.chmod(0o700)
        shutil.rmtree(profile, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="plugin_check", description=__doc__.split("\n\n")[0])
    parser.add_argument("folder")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of sentences")
    args = parser.parse_args(argv)
    result = check(args.folder)
    code = result.pop("code")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    elif result["ok"]:
        plugin = result["plugin"]
        print(f"OK: {plugin['id']} {plugin['version']} would be offered for review. Tools: {', '.join(result['tools']) or 'none'}. "
              f"Reaches: {', '.join(plugin['network'] or []) or 'no websites'}.")
    else:
        print("Refused: " + result["message"])
        if result["fix"]:
            print("For the plugin's maker: " + (result["where"] + ": " if result["where"] else "") + result["fix"])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
