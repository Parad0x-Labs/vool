"""Every automated browser launch carries the keychain-isolation flags. All of them.

A disposable ``--user-data-dir`` is not enough. On macOS, Chrome with a fresh profile
but a real password store still initialises "Chrome Safe Storage" against the operator's
Keychain and raises a dialog *per launch* — which is what an automated test loop turns
into a stream of prompts. ``--use-mock-keychain`` and ``--password-store=basic`` are what
prevent it.

They were defined and honoured in ``core/vool_browser/engine.py`` and nowhere else, while
two other paths built their own argv: ``tools/browser/browser_render.py`` (every headless
render, reachable from web search) and ``installer/bundle/vool_window.py`` (the app-window
fallback). Both passed a disposable profile and neither passed these. A launch path that
builds its own argv is a launch path that forgets, so the flags are now one tuple that
every builder imports, and this file fails if a builder stops importing it.

The interactive case is preserved by construction and asserted below: VOOL never launches
the operator's own Chrome at all. Every path passes ``--user-data-dir``, and the window
shim refuses outright rather than falling back to the default browser. The operator's own
Chrome, started by the operator, is untouched by any of this.
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys

import pytest

from core.vool_browser.engine import (
    CREDENTIAL_ISOLATION_FLAGS,
    automated_launch_flags,
    isolation_launch_flags,
)

#: Spelled out here on purpose, NOT read from the authority. A guard that checks each
#: builder against the same tuple it is testing passes vacuously when that tuple is the
#: thing that broke — measured: stripping --use-mock-keychain from the authority left
#: every per-builder assertion green because the loop simply had one less flag to check.
REQUIRED_FLAGS = ("--use-mock-keychain", "--password-store=basic")

REPO = pathlib.Path(__file__).resolve().parents[1]
WINDOW_SHIM = REPO / "installer" / "bundle" / "vool_window.py"


def _load_window_shim():
    spec = importlib.util.spec_from_file_location("_vool_window_under_test", WINDOW_SHIM)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _render_argv() -> tuple[list[str], str | None]:
    from tools.browser.browser_render import _chrome_argv

    return _chrome_argv(
        "/nonexistent/chrome",
        url="https://example.invalid/",
        user_agent="ua",
        virtual_time_ms=100,
        screenshot_path=None,
    )


def _window_flags() -> list[str]:
    return _load_window_shim()._disposable_profile_flags()


def _cleanup(flags: list[str], profile: str | None = None) -> None:
    for flag in flags:
        if flag.startswith("--user-data-dir="):
            shutil.rmtree(flag.split("=", 1)[1], ignore_errors=True)
    if profile:
        shutil.rmtree(profile, ignore_errors=True)


# ── every builder, named individually ────────────────────────────────────────


def test_the_two_flags_are_exactly_what_the_authority_says() -> None:
    assert set(CREDENTIAL_ISOLATION_FLAGS) == set(REQUIRED_FLAGS)


@pytest.mark.parametrize("builder", ["engine.isolation", "engine.automated", "browser_render", "window_shim"])
def test_every_automated_launch_builder_carries_both_flags(builder: str) -> None:
    """Named per builder so a regression says WHICH launch path went bare."""
    profile = None
    if builder == "engine.isolation":
        flags = isolation_launch_flags(profile_dir="/tmp/vool-test-profile")
    elif builder == "engine.automated":
        flags = automated_launch_flags(profile_dir="/tmp/vool-test-profile")
    elif builder == "browser_render":
        flags, profile = _render_argv()
    else:
        flags = _window_flags()
    try:
        assert flags, f"{builder} produced no launch flags at all"
        for required in REQUIRED_FLAGS:
            assert required in flags, f"{builder} launches without {required}"
        assert any(f.startswith("--user-data-dir=") for f in flags), (
            f"{builder} passes no --user-data-dir, so Chrome would use the operator's own profile"
        )
    finally:
        _cleanup(flags if builder in {"browser_render", "window_shim"} else [], profile)


def test_no_launch_path_reaches_the_operators_default_profile() -> None:
    """The interactive guarantee: VOOL never drives the operator's own Chrome."""
    argv, profile = _render_argv()
    try:
        assert any(a.startswith("--user-data-dir=") for a in argv)
    finally:
        _cleanup(argv, profile)
    flags = _window_flags()
    try:
        assert any(a.startswith("--user-data-dir=") for a in flags)
    finally:
        _cleanup(flags)


# ── the authority stays singular ─────────────────────────────────────────────


def test_the_flag_literals_appear_in_exactly_one_source_of_truth() -> None:
    """A copied literal is a second authority that drifts silently.

    Production code may reference the tuple; it may not re-type the strings. Tests are
    exempt: asserting on the literal is how a guard proves the flag is really there.
    """
    out = subprocess.run(
        ["git", "grep", "-l", "--fixed-strings", "--use-mock-keychain", "--", "*.py"],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    ).stdout.split()
    offenders = [
        f for f in out
        if not f.startswith("tests/") and f != "core/vool_browser/engine.py"
    ]
    assert not offenders, f"the flag literal is re-typed outside the authority: {offenders}"


def test_every_file_that_passes_a_profile_also_imports_the_flags() -> None:
    """Mechanical, so a NEW launch site cannot quietly skip them."""
    passers = subprocess.run(
        ["git", "grep", "-l", "--fixed-strings", "--user-data-dir=", "--", "*.py"],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    ).stdout.split()
    bare = []
    for rel in passers:
        if rel.startswith("tests/") or rel == "core/vool_browser/engine.py":
            continue
        if "CREDENTIAL_ISOLATION_FLAGS" not in (REPO / rel).read_text(encoding="utf-8", errors="ignore"):
            bare.append(rel)
    assert not bare, f"these launch a browser profile without importing the isolation flags: {bare}"


def test_the_window_shim_refuses_rather_than_launching_without_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed. The shim is stdlib-only, so its import of the authority is guarded —
    and a guard that degrades to launching bare would be worse than no guard."""
    module = _load_window_shim()
    monkeypatch.setattr(module, "_credential_isolation_flags", lambda: [])
    assert module._disposable_profile_flags() == []


# ── the flags are load-bearing ───────────────────────────────────────────────


@pytest.mark.parametrize("dropped", ["--use-mock-keychain", "--password-store=basic"])
def test_sabotage_dropping_either_flag_is_detected(
    monkeypatch: pytest.MonkeyPatch, dropped: str
) -> None:
    """Remove one flag from the authority; the render builder must be seen to lose it."""
    import tools.browser.browser_render as render

    survivors = tuple(f for f in CREDENTIAL_ISOLATION_FLAGS if f != dropped)
    monkeypatch.setattr(render, "CREDENTIAL_ISOLATION_FLAGS", survivors)
    argv, profile = _render_argv()
    try:
        assert dropped not in argv, (
            "sabotage no-op: the flag survives with the authority stripped, so the "
            "builder is not reading it from there"
        )
        # And the per-builder guard would have caught it too, because that one asserts
        # against REQUIRED_FLAGS rather than against the tuple under sabotage.
        assert not all(f in argv for f in REQUIRED_FLAGS)
    finally:
        _cleanup(argv, profile)


# ── the pin beats the memo, or nothing above holds ───────────────────────────


def _fake_binary(tmp_path: pathlib.Path, name: str) -> str:
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    return str(exe)


def test_the_pinned_binary_wins_even_after_another_browser_was_already_resolved(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect this pins is what launched the operator's real Chrome.

    `find_browser_binary` memoizes, and it used to consult the memo BEFORE reading
    `VOOL_BROWSER_BINARY`. So the first resolution in a process won permanently and the
    pin was silently ignored from then on. The unattended preflight pins a bundled
    Chromium precisely to avoid the installed Chrome's first-run and "Chrome Safe
    Storage" Keychain initialisation — and that pin stopped being honoured the moment
    anything else had already located a browser. Measured consequence in a test batch:
    the real Chrome launched repeatedly, one Keychain dialog each, while a test that had
    pinned a harmless fake sat waiting for it and timed out.
    """
    from tools.browser import browser_render

    first = _fake_binary(tmp_path, "first-browser")
    second = _fake_binary(tmp_path, "second-browser")

    browser_render.reset_browser_binary_cache_for_test()
    monkeypatch.setenv("VOOL_BROWSER_BINARY", first)
    assert browser_render.find_browser_binary() == first

    # A different pin, same process, memo already warm.
    monkeypatch.setenv("VOOL_BROWSER_BINARY", second)
    assert browser_render.find_browser_binary() == second, (
        "the memo overrode the explicit pin: an override a cache can defeat is not an override"
    )


def test_clearing_the_pin_does_not_strand_the_process_on_the_pinned_binary(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsetting the pin must fall back to discovery, not keep serving the pinned path."""
    from tools.browser import browser_render

    pinned = _fake_binary(tmp_path, "pinned-browser")
    browser_render.reset_browser_binary_cache_for_test()
    monkeypatch.setenv("VOOL_BROWSER_BINARY", pinned)
    assert browser_render.find_browser_binary() == pinned

    monkeypatch.delenv("VOOL_BROWSER_BINARY", raising=False)
    assert browser_render.find_browser_binary() != pinned or browser_render.find_browser_binary() is None


def test_memoization_is_still_in_place_for_the_expensive_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix must not turn every call into a filesystem walk."""
    from tools.browser import browser_render

    browser_render.reset_browser_binary_cache_for_test()
    monkeypatch.delenv("VOOL_BROWSER_BINARY", raising=False)
    calls = {"n": 0}
    real_glob = browser_render.glob.glob

    def _counting(*a, **k):
        calls["n"] += 1
        return real_glob(*a, **k)

    monkeypatch.setattr(browser_render.glob, "glob", _counting)
    browser_render.find_browser_binary()
    after_first = calls["n"]
    browser_render.find_browser_binary()
    assert calls["n"] == after_first, "the discovery path is no longer memoized"
