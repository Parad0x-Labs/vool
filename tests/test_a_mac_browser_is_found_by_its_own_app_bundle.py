"""A browser app on a Mac is found by the program its own Info.plist names.

Brave's program is "Brave Browser", Chrome's is "Google Chrome": a hand-typed path list once sent
Brave to a "Google Chrome" program its bundle does not have. The program name now comes from each
bundle, for every app, and the page renderer uses the same list.

Which apps are looked for is a second question: only browsers whose own background contacts were
measured and closed. Brave and Microsoft Edge are not among them yet, so a computer that has only
one of those has no engine for VOOL: not for the window, not for VOOL's own lookups.
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
    chromium = _app(tmp_path, "Chromium", "Chromium")
    renamed = _app(tmp_path, "Google Chrome", "Chrome Launcher")
    assert engine.macos_app_programs(folders=(str(tmp_path),)) == [str(renamed), str(chromium)]
    # The reader itself is the same for any app it is asked about.
    brave = _app(tmp_path, "Brave Browser", "Brave Browser")
    assert engine.macos_app_program(tmp_path / "Brave Browser.app") == str(brave)


def test_a_bundle_with_no_plist_falls_back_to_the_app_name(tmp_path):
    chromium = _app(tmp_path, "Chromium", None, plist=False)
    assert engine.macos_app_program(tmp_path / "Chromium.app") == str(chromium)


def test_a_missing_or_unrunnable_program_is_not_offered(tmp_path):
    _app(tmp_path, "Google Chrome", "Google Chrome", runnable=False)
    (tmp_path / "Chromium.app" / "Contents").mkdir(parents=True)
    (tmp_path / "Chromium.app" / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Chromium"}))
    assert engine.macos_app_programs(folders=(str(tmp_path),)) == []


def test_a_plist_naming_a_path_is_not_followed(tmp_path):
    bundle = tmp_path / "Chromium.app" / "Contents"
    (bundle / "MacOS").mkdir(parents=True)
    (bundle / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "../../../evil"}))
    assert engine.macos_app_program(tmp_path / "Chromium.app") is None


def test_the_person_s_own_applications_folder_is_searched(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    chrome = _app(tmp_path / "Applications", "Google Chrome", "Google Chrome")
    assert str(chrome) in engine.macos_app_programs(folders=("~/Applications",))


def _mac_with(tmp_path, monkeypatch, *apps: str) -> dict[str, Path]:
    monkeypatch.delenv("VOOL_BROWSER_ENGINE", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(engine, "MACOS_APP_FOLDERS", (str(tmp_path),))
    return {app: _app(tmp_path, app, app) for app in apps}


def _linux_with(tmp_path, monkeypatch, *programs: str) -> dict[str, Path]:
    monkeypatch.delenv("VOOL_BROWSER_ENGINE", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    made = {}
    for name in programs:
        made[name] = tmp_path / name
        made[name].write_text("#!/bin/sh\n")
        made[name].chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    return made


def test_a_mac_with_only_brave_or_edge_has_no_engine_for_vool(tmp_path, monkeypatch):
    _mac_with(tmp_path, monkeypatch, "Brave Browser", "Microsoft Edge")
    assert engine.macos_app_programs() == []
    assert engine.find_engine_binary() is None


def test_a_mac_with_chrome_beside_them_uses_chrome(tmp_path, monkeypatch):
    here = _mac_with(tmp_path, monkeypatch, "Brave Browser", "Microsoft Edge", "Google Chrome")
    assert engine.find_engine_binary() == str(here["Google Chrome"])


def test_a_linux_computer_with_only_brave_or_edge_has_no_engine_for_vool(tmp_path, monkeypatch):
    _linux_with(tmp_path, monkeypatch, "brave-browser", "brave-browser-stable", "microsoft-edge", "microsoft-edge-stable")
    assert engine.find_engine_binary() is None


def test_a_linux_computer_with_chromium_beside_them_uses_chromium(tmp_path, monkeypatch):
    here = _linux_with(tmp_path, monkeypatch, "brave-browser", "microsoft-edge", "chromium")
    assert engine.find_engine_binary() == str(here["chromium"])


def test_vools_own_lookups_refuse_them_too(tmp_path, monkeypatch):
    """The page renderer behind VOOL's lookups has its own search for an engine. It reads the same
    lists, so a browser that is refused for the window is refused there as well."""
    from tools.browser import browser_render

    monkeypatch.delenv("VOOL_BROWSER_BINARY", raising=False)
    monkeypatch.setattr(browser_render, "_playwright_cache_dir", lambda: str(tmp_path / "no-cache"))
    browser_render.reset_browser_binary_cache_for_test()
    try:
        _linux_with(tmp_path, monkeypatch, "brave-browser", "microsoft-edge")
        assert browser_render.find_browser_binary() is None
        here = _linux_with(tmp_path, monkeypatch, "brave-browser", "microsoft-edge", "google-chrome")
        browser_render.reset_browser_binary_cache_for_test()
        assert browser_render.find_browser_binary() == str(here["google-chrome"])
    finally:
        browser_render.reset_browser_binary_cache_for_test()


def test_the_sentence_for_no_engine_names_what_is_needed_in_every_language():
    from core.i18n import catalog_for
    from core.i18n.locales import ui_catalog_tags

    codes = ui_catalog_tags()
    assert len(codes) >= 17
    english = catalog_for("en").text("live.engine_missing")
    for code in codes:
        said = catalog_for(code).text("live.engine_missing")
        for name in ("Google Chrome", "Chromium", "Brave", "Edge"):
            assert name in said, (code, name)
        assert code == "en" or said != english, code


def test_no_module_types_its_own_mac_browser_program_path():
    root = Path(__file__).resolve().parents[1]
    for path in ("core/vool_browser/engine.py", "core/vool_browser/desk.py", "tools/browser/browser_render.py"):
        text = (root / path).read_text(encoding="utf-8")
        assert "/Applications/" not in text, path
