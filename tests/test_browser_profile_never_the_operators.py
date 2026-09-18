"""VOOL never renders through the operator's own browser profile.

Two production paths did, and both were the default on an ordinary interactive
macOS run.

``tools/browser/browser_render.py`` only passed ``--user-data-dir`` when
``VOOL_BROWSER_PROFILE_DIR`` was set. That env is set by the unattended
preflight in non-interactive modes only, and **no launcher sets
``VOOL_UNATTENDED``** — so on every operator launch the branch was false, no
profile was passed, and Chrome used the default profile: the operator's cookies,
logins and live session. It also preferred the operator's installed Chrome over
the bundled build. Reachable from ``web.search``, with
``ALLOW_BROWSER_FALLBACK=1`` written into the generated launcher by the installer.

``installer/bundle/vool_window.py`` ran ``open -na "Google Chrome" --args
--app=<url>`` with no ``--user-data-dir``, no ``--no-first-run`` and no
``--no-default-browser-check`` — while its Windows sibling ``vool-open.ps1`` has
always passed all three.

The guard was a conditional feature keyed on an env var. It is an INVARIANT: a
VOOL-owned disposable profile always, or a typed refusal. The env only relocates
the throwaway root.

Every check here runs with the env UNSET — the production default, which the
existing browser-isolation suite never exercised.
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _production_default_env(monkeypatch: pytest.MonkeyPatch):
    """No isolation env set — exactly what an operator's own launch looks like."""
    for key in ("VOOL_BROWSER_PROFILE_DIR", "VOOL_BROWSER_BINARY", "VOOL_UNATTENDED"):
        monkeypatch.delenv(key, raising=False)


def _argv(url: str = "https://example.com/"):
    from tools.browser.browser_render import _chrome_argv, find_browser_binary

    binary = find_browser_binary()
    if not binary:
        pytest.skip("no Chromium-family browser on this machine")
    return _chrome_argv(
        binary, url, user_agent="ua", virtual_time_ms=9000, screenshot_path=""
    )


# ── the render lane ──────────────────────────────────────────────────────────


def test_a_disposable_profile_is_always_passed_with_the_env_unset() -> None:
    """The production default. This is the case the old suite never covered."""
    import shutil

    argv, profile = _argv()
    try:
        flags = [a for a in argv if a.startswith("--user-data-dir=")]
        assert flags, f"no --user-data-dir in argv: {argv}"
        assert profile, "no profile directory was returned for cleanup"
        assert flags[0] == f"--user-data-dir={profile}"
        assert os.path.isdir(profile)
        # VOOL-owned and throwaway: under the system temp root, not the operator's
        # browser support directory.
        assert profile.startswith(tempfile.gettempdir())
        assert "vool-browser-profile-" in profile
        assert "Application Support" not in profile
        assert "Google/Chrome" not in profile
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def test_the_first_run_switches_ride_with_it() -> None:
    import shutil

    argv, profile = _argv()
    try:
        assert "--no-first-run" in argv
        assert "--no-default-browser-check" in argv
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def test_every_render_gets_a_DIFFERENT_profile() -> None:
    """Disposable means per-render, so one page cannot see another's cookies."""
    import shutil

    _, first = _argv("https://example.com/a")
    _, second = _argv("https://example.com/b")
    try:
        assert first != second, "two renders shared one profile directory"
    finally:
        shutil.rmtree(first, ignore_errors=True)
        shutil.rmtree(second, ignore_errors=True)


def test_the_bundled_build_is_preferred_over_the_operators_installed_chrome() -> None:
    """Order was inverted deliberately.

    The operator's Chrome was preferred "because its fingerprint matches ordinary
    traffic" — and that same choice is what made a missing --user-data-dir open
    their real profile, and what hung on a fresh profile through Chrome's own
    first-run / Keychain initialisation. The bundled build does neither.
    """
    from tools.browser.browser_render import _playwright_cache_dir, find_browser_binary

    cache = _playwright_cache_dir()
    if not os.path.isdir(cache):
        pytest.skip("no bundled Chromium on this machine to prefer")
    binary = find_browser_binary()
    assert binary, "no browser found at all"
    assert binary.startswith(cache), (
        f"the operator's installed browser was chosen over the bundled build: {binary}"
    )


def test_an_uncreatable_profile_is_a_typed_refusal_not_the_real_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The only alternative to a throwaway profile is the operator's real one.

    So there is no fallback: it refuses.
    """
    from tools.browser import browser_render as br

    def _boom(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(br.tempfile, "mkdtemp", _boom)
    binary = br.find_browser_binary()
    if not binary:
        pytest.skip("no Chromium-family browser on this machine")
    with pytest.raises(br.BrowserProfileUnavailableError) as caught:
        br._chrome_argv(binary, "https://example.com/", user_agent="ua", virtual_time_ms=9000, screenshot_path="")
    assert "operator" in str(caught.value).lower()


# ── the packaged window fallback ─────────────────────────────────────────────


def test_the_window_fallback_carries_all_three_isolation_flags() -> None:
    """Lane parity with Windows, which has always passed all three."""
    import importlib.util
    import shutil

    spec = importlib.util.spec_from_file_location(
        "_vool_window_probe",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "installer",
            "bundle",
            "vool_window.py",
        ),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    flags = module._disposable_profile_flags()
    profile = ""
    try:
        assert flags, "no isolation flags were produced"
        user_data = [f for f in flags if f.startswith("--user-data-dir=")]
        assert user_data, flags
        profile = user_data[0].split("=", 1)[1]
        assert "vool-window-profile-" in profile
        assert "--no-first-run" in flags
        assert "--no-default-browser-check" in flags
    finally:
        if profile:
            shutil.rmtree(profile, ignore_errors=True)


# ── the guard is load-bearing ────────────────────────────────────────────────


def test_sabotage_making_the_profile_conditional_again_reopens_the_real_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore the old env-gated shape and the default profile comes straight back."""
    from tools.browser import browser_render as br

    def _old_shape(binary, url, *, user_agent, virtual_time_ms, screenshot_path):
        argv = [binary, "--headless=new", "--no-first-run", "--no-default-browser-check"]
        profile_root = str(os.environ.get("VOOL_BROWSER_PROFILE_DIR") or "").strip()
        profile_dir = None
        if profile_root:  # SABOTAGE: the old conditional
            profile_dir = tempfile.mkdtemp(prefix="profile-", dir=profile_root)
            argv.append(f"--user-data-dir={profile_dir}")
        argv += ["--dump-dom", url]
        return argv, profile_dir

    monkeypatch.setattr(br, "_chrome_argv", _old_shape)
    argv, profile = br._chrome_argv(
        "/bin/true", "https://example.com/", user_agent="ua", virtual_time_ms=9000, screenshot_path=""
    )
    assert profile is None
    assert not [a for a in argv if a.startswith("--user-data-dir=")], (
        "sabotage no-op: the old env-gated shape still produced a profile with the env "
        "unset, so the invariant is not what closes this"
    )
