"""A browser app on a Mac is found by the program its own Info.plist names.

Brave's program is "Brave Browser", Chrome's is "Google Chrome": a hand-typed path list once sent
Brave to a "Google Chrome" program its bundle does not have, so Brave was never found. The program
name now comes from each bundle, for every app, and the page renderer uses the same list.
"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

from core.vool_browser import engine


def _app(folder: Path, app: str, program: str | None, *, plist: bool = True, runnable: bool = True) -> Path:
    bundle = folder / f"{app}.app" / "Contents"
    (bundle / "MacOS").mkdir(parents=True)
    if plist:
        (bundle / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": program or app}))
    target = bundle / "MacOS" / (program or app)
    target.write_text("#!/bin/sh\n")
    target.chmod(0o755 if runnable else 0o644)
    return target


def test_each_app_is_found_by_the_program_its_bundle_names(tmp_path):
    brave = _app(tmp_path, "Brave Browser", "Brave Browser")
    renamed = _app(tmp_path, "Microsoft Edge", "Edge Launcher")
    assert engine.macos_app_programs(folders=(str(tmp_path),)) == [str(renamed), str(brave)]


def test_a_bundle_with_no_plist_falls_back_to_the_app_name(tmp_path):
    chromium = _app(tmp_path, "Chromium", None, plist=False)
    assert engine.macos_app_program(tmp_path / "Chromium.app") == str(chromium)


def test_a_missing_or_unrunnable_program_is_not_offered(tmp_path):
    _app(tmp_path, "Google Chrome", "Google Chrome", runnable=False)
    (tmp_path / "Brave Browser.app" / "Contents").mkdir(parents=True)
    (tmp_path / "Brave Browser.app" / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Brave Browser"}))
    assert engine.macos_app_programs(folders=(str(tmp_path),)) == []


def test_a_plist_naming_a_path_is_not_followed(tmp_path):
    bundle = tmp_path / "Chromium.app" / "Contents"
    (bundle / "MacOS").mkdir(parents=True)
    (bundle / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "../../../evil"}))
    assert engine.macos_app_program(tmp_path / "Chromium.app") is None


def test_the_person_s_own_applications_folder_is_searched(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    brave = _app(tmp_path / "Applications", "Brave Browser", "Brave Browser")
    assert str(brave) in engine.macos_app_programs(folders=("~/Applications",))


def test_find_engine_binary_on_a_mac_returns_brave(tmp_path, monkeypatch):
    brave = _app(tmp_path, "Brave Browser", "Brave Browser")
    monkeypatch.delenv("VOOL_BROWSER_ENGINE", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(engine, "MACOS_APP_FOLDERS", (str(tmp_path),))
    assert engine.find_engine_binary() == str(brave)


def test_no_module_types_its_own_mac_browser_program_path():
    root = Path(__file__).resolve().parents[1]
    for path in ("core/vool_browser/engine.py", "core/vool_browser/desk.py", "tools/browser/browser_render.py"):
        text = (root / path).read_text(encoding="utf-8")
        assert "/Applications/" not in text, path
