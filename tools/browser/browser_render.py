from __future__ import annotations

import contextlib
import glob
import html as html_module
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Any

from core import policy_engine
from core.remote_fetch_policy import note_remote_fetch_attempt, remote_fetch_forbidden
from core.vool_browser.engine import CREDENTIAL_ISOLATION_FLAGS
from tools.web.browser_identity import DESKTOP_ACCEPT_LANGUAGE, DESKTOP_USER_AGENT

_CAPTCHA_RE = re.compile(
    r"(captcha|are you human|robot check|bots use duckduckgo too|anomaly-modal|select all squares containing)",
    re.IGNORECASE,
)
_LOGIN_RE = re.compile(r"(sign in|log in|password)", re.IGNORECASE)

# A real block page is SHORT: it is almost entirely the block notice. This is the same guard
# `tools/web/http_fetch.py` already carries, and it was missing here -- measured 2026-08-17, a
# perfectly good DuckDuckGo results page (7,469 characters of visible text, ten real results)
# classified as `captcha` because the string `anomaly-modal` appears ONCE in DuckDuckGo's bundled
# stylesheet on every page it serves, challenge or not. Scanning the raw markup of a 250 KB
# document for a CSS class name meant every successful render was thrown away, and the search
# provider that depends on this returned zero hits with no error anywhere.
_BLOCK_PAGE_MAX_CHARS = 2500


def _classify_rendered_content(html_text: str, text: str) -> str:
    visible = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(visible) > _BLOCK_PAGE_MAX_CHARS:
        # Too much real content to be a block notice. "sign in" in a header and a class name in a
        # stylesheet are not evidence of a wall.
        return "ok"
    # Markup markers (a class name like `anomaly-modal`) only mean anything on a page that is
    # itself small enough to BE the notice.
    lowered = f"{html_text}\n{visible}".lower() if len(str(html_text or "")) <= _BLOCK_PAGE_MAX_CHARS else visible.lower()
    if _CAPTCHA_RE.search(lowered):
        return "captcha"
    if _LOGIN_RE.search(lowered):
        return "login_wall"
    return "ok"


# --------------------------------------------------------------------------------------------
# Locating a browser without installing one
# --------------------------------------------------------------------------------------------
#
# This machine holds live keypairs, so `pip install playwright` (or any install) is not an option --
# a previous machine was drained by a malicious npm postinstall that walked the filesystem for
# keypairs. The whole design here is that it needs no install: a Chromium-family browser is already
# on the box, and `--headless=new --dump-dom` drives it over a plain subprocess pipe.
#
# Order matters, and it was INVERTED. A real Google Chrome used to be preferred over the bundled
# Playwright build "because it is the browser the user actually runs, so it is the one whose
# fingerprint matches ordinary traffic". That reasoning is sound about fingerprints and wrong about
# everything else it caused:
#
#   * the operator's installed Chrome, launched with no --user-data-dir, uses their DEFAULT
#     profile -- cookies, logins, session, history;
#   * and a fresh profile under that same installed Chrome is what hung on 2026-08-17, most
#     plausibly its own first-run / "Chrome Safe Storage" Keychain initialisation -- a dialog
#     the unattended law forbids.
#
# The bundled build (chrome-headless-shell / Chrome for Testing) does neither. It is preferred now,
# and the operator's browser is the last resort rather than the first choice.

_MACOS_APP_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)

_LINUX_PATH_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "brave-browser",
)

# Playwright's own browser downloads. The PYTHON package is absent and must stay absent, but its
# cache directory may already hold a chromium build from some other tool -- a binary on disk is a
# binary on disk, and using it costs no install.
_PLAYWRIGHT_CACHE_GLOBS = (
    "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
    "chromium-*/chrome-linux*/chrome",
    "chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell",
)

_browser_binary_cache: dict[str, str | None] = {}


def _playwright_cache_dir() -> str:
    override = str(os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")).strip()
    if override and override not in {"0", "1"}:
        return override
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/ms-playwright")
    return os.path.expanduser("~/.cache/ms-playwright")


def reset_browser_binary_cache_for_test() -> None:
    """Drop the memoized browser path (the locator stats the filesystem, so it is cached)."""

    _browser_binary_cache.clear()


def find_browser_binary() -> str | None:
    """Return the path of a Chromium-family browser already on this machine, or None.

    None is a legitimate answer, not an error: a machine with no browser must behave exactly as it
    did before this engine existed, which is why every caller treats None as `missing_dependency`
    rather than raising.
    """

    # The explicit pin is read BEFORE the memo, and the memo is keyed by it.
    #
    # This used to check `_browser_binary_cache["path"]` first, so the first resolution
    # in a process won permanently and VOOL_BROWSER_BINARY was silently ignored from
    # then on. An override a cache can defeat is not an override, and the consequences
    # were real: the unattended preflight pins a bundled Chromium precisely to avoid the
    # installed Chrome's first-run and "Chrome Safe Storage" Keychain initialisation, and
    # that pin stopped being honoured the moment anything else had already located a
    # browser. In an automated test batch the effect was the operator's own installed
    # Chrome being launched repeatedly -- one Keychain dialog per launch -- while a test
    # that had pinned a harmless fake binary sat waiting for it and timed out.
    #
    # Memoization is kept, because the miss path stats the filesystem and globs a cache
    # directory. Only its key changes.
    override = str(os.getenv("VOOL_BROWSER_BINARY", "")).strip()
    cache_key = f"override:{override}" if override else "discovered"
    if cache_key in _browser_binary_cache:
        return _browser_binary_cache[cache_key]

    found: str | None = None

    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        found = override

    if found is None:
        cache_dir = _playwright_cache_dir()
        for pattern in _PLAYWRIGHT_CACHE_GLOBS:
            matches = sorted(glob.glob(os.path.join(cache_dir, pattern)), reverse=True)
            hit = next((m for m in matches if os.path.isfile(m) and os.access(m, os.X_OK)), None)
            if hit:
                found = hit
                break

    if found is None:
        for candidate in _MACOS_APP_CANDIDATES:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                found = candidate
                break

    if found is None:
        for name in _LINUX_PATH_CANDIDATES:
            resolved = shutil.which(name)
            if resolved:
                found = resolved
                break

    _browser_binary_cache[cache_key] = found
    return found


# --------------------------------------------------------------------------------------------
# The Chrome subprocess engine
# --------------------------------------------------------------------------------------------

# Measured 2026-08-17 on this machine, four renders of a DuckDuckGo results page: 4.0s, 4.2s, 4.8s,
# 6.6s (one outlier at 9.4s). `timeout_ms` is the TOTAL subprocess wall clock, launch included --
# no hidden slack on top, so a caller working against a shared deadline can size it exactly.
_DEFAULT_VIRTUAL_TIME_MS = 9_000
# Leave this much of the wall clock for process launch and teardown when picking the virtual-time
# budget, so the page gets its render time and the kill still lands inside the caller's deadline.
_LAUNCH_RESERVE_MS = 3_000

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r'href="(https?://[^"\s]+)"', re.IGNORECASE)


def _cleanup_isolated_profile(profile_dir: str | None) -> None:
    """Remove the fresh per-render profile; it must never accumulate or be reused."""
    if not profile_dir:
        return
    shutil.rmtree(profile_dir, ignore_errors=True)


def _is_headless_shell(binary: str) -> bool:
    return os.path.basename(binary).startswith("chrome-headless-shell")


class BrowserProfileUnavailableError(RuntimeError):
    """No disposable profile could be created, so nothing was launched.

    Typed rather than a fall back: the only alternative to a VOOL-owned throwaway
    profile is the operator's real one, and that is never an acceptable degradation.
    """


def _chrome_argv(
    binary: str,
    url: str,
    *,
    user_agent: str,
    virtual_time_ms: int,
    screenshot_path: str | None,
) -> tuple[list[str], str | None]:
    """Build the argv; also returns the isolated profile dir to clean up, if one was made."""
    argv = [binary]
    # chrome-headless-shell IS the headless build; it does not accept the switch that turns
    # headless on in a full browser.
    if not _is_headless_shell(binary):
        argv.append("--headless=new")
    argv += [
        "--disable-gpu",
        "--no-sandbox",
        # Credential isolation, imported from the ONE authority rather than repeated.
        # A disposable --user-data-dir is NOT enough on macOS: Chrome still initialises
        # "Chrome Safe Storage" against the real Keychain and raises a dialog per
        # launch. Measured as repeated prompts during an automated test loop.
        *CREDENTIAL_ISOLATION_FLAGS,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--mute-audio",
        f"--accept-lang={DESKTOP_ACCEPT_LANGUAGE}",
        f"--virtual-time-budget={int(virtual_time_ms)}",
        f"--user-agent={user_agent}",
    ]
    # Profile isolation seam (C15, 2026-09-03): when the unattended preflight has set
    # VOOL_BROWSER_PROFILE_DIR, EVERY render gets a FRESH empty subdirectory of it
    # (--user-data-dir) — no cookies, no logins, no signed-in profile, no "Chrome Safe
    # Storage" Keychain item. With the env unset the historical behaviour stands: the
    # measured 2026-08-17 hang of a fresh temp profile under the installed Chrome was
    # most plausibly that browser's own first-run/Keychain initialisation, which the
    # preflight's bundled Chromium (headless-shell / Chrome for Testing) does not do;
    # unattended runs therefore also get VOOL_BROWSER_BINARY pinned to that build.
    # ALWAYS a VOOL-owned disposable profile. This used to be conditional on
    # VOOL_BROWSER_PROFILE_DIR being set -- an env no product launcher sets, because
    # the preflight only sets it in non-interactive modes and nothing sets
    # VOOL_UNATTENDED. So on every ordinary operator run the `if` was false, no
    # --user-data-dir was passed, and Chrome used the operator's DEFAULT profile:
    # their cookies, their logins, their live session. Reachable from web.search,
    # with ALLOW_BROWSER_FALLBACK=1 baked into the installed launcher.
    #
    # The guard was a conditional feature keyed on an env var; it is an INVARIANT.
    # The env now only RELOCATES the disposable root, and a profile that cannot be
    # created is a typed refusal -- never a silent fall back to the real profile.
    profile_root = str(os.environ.get("VOOL_BROWSER_PROFILE_DIR") or "").strip()
    try:
        profile_dir = tempfile.mkdtemp(prefix="vool-browser-profile-", dir=profile_root or None)
    except OSError as exc:
        raise BrowserProfileUnavailableError(
            "no disposable browser profile could be created, so no browser was launched: "
            f"{type(exc).__name__}. VOOL never renders through the operator's own browser "
            "profile."
        ) from exc
    argv.append(f"--user-data-dir={profile_dir}")
    if screenshot_path:
        argv += ["--window-size=1280,2400", f"--screenshot={screenshot_path}"]
    argv += ["--dump-dom", url]
    return argv, profile_dir


def _run_chrome(argv: list[str], *, timeout_s: float) -> tuple[int, bytes]:
    """Run the browser and return (returncode, stdout). Kills the whole process group on timeout.

    `start_new_session` matters: Chrome forks helper processes (GPU, network, renderer), and killing
    only the direct child leaves those helpers alive holding the port and the profile lock. On a
    long-running daemon that leak compounds until the machine runs out of processes.
    """

    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout_s)
        return process.returncode, stdout or b""
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()
        with contextlib.suppress(Exception):
            process.communicate(timeout=5)
        raise


def _chrome_render_unchecked(
    url: str,
    *,
    timeout_ms: int = 20_000,
    screenshot_path: str | None = None,
    user_agent: str | None = None,
    binary: str | None = None,
) -> dict[str, Any]:
    """Render `url` in a real browser via `--dump-dom`. Returns; never raises.

    The remote-fetch door is the CALLER's job here -- `browser_render` and `chrome_render` each
    open it once, so routing through this helper does not double-count an attempt.

    Shape note: `final_url` is the requested URL. `--dump-dom` prints the serialized DOM and does
    not report the address bar, so a redirect is NOT visible to this engine, whereas the Playwright
    path reports `page.url`. That is a real fidelity gap between the two engines and it is stated
    rather than papered over.
    """

    selected = binary or find_browser_binary()
    if not selected:
        return {"status": "missing_dependency", "final_url": url}

    timeout_s = max(5.0, float(timeout_ms) / 1000.0)
    virtual_time_ms = min(_DEFAULT_VIRTUAL_TIME_MS, max(1_000, int(timeout_s * 1000) - _LAUNCH_RESERVE_MS))
    argv, profile_dir = _chrome_argv(
        selected,
        url,
        user_agent=user_agent or DESKTOP_USER_AGENT,
        virtual_time_ms=virtual_time_ms,
        screenshot_path=screenshot_path,
    )

    try:
        returncode, raw = _run_chrome(argv, timeout_s=timeout_s)
    except subprocess.TimeoutExpired:
        _cleanup_isolated_profile(profile_dir)
        return {"status": "browser_timeout", "final_url": url}
    except (OSError, ValueError) as exc:
        _cleanup_isolated_profile(profile_dir)
        return {"status": f"browser_error:{type(exc).__name__}", "final_url": url}
    _cleanup_isolated_profile(profile_dir)

    html_text = raw.decode("utf-8", errors="ignore")
    if not html_text.strip():
        # A zero-byte DOM with a clean exit code is still a failed render; reporting it as "ok"
        # with empty text would let an empty page be cited as evidence.
        return {"status": f"browser_empty_dom:rc{returncode}", "final_url": url}

    from tools.web.http_fetch import strip_html

    text = html_module.unescape(strip_html(html_text))
    rendered_status = _classify_rendered_content(html_text, text)
    if rendered_status in {"captcha", "login_wall"}:
        return {"status": rendered_status, "final_url": url}

    title_match = _TITLE_RE.search(html_text)
    title = html_module.unescape(re.sub(r"\s+", " ", title_match.group(1))).strip() if title_match else ""
    links = [html_module.unescape(href) for href in _HREF_RE.findall(html_text)][:200]

    return {
        "status": "ok",
        "final_url": url,
        "title": title,
        "text": text[:200000],
        "html": html_text[:2000000],
        "links": links,
        "screenshot_path": screenshot_path if (screenshot_path and os.path.isfile(screenshot_path)) else None,
    }


def chrome_render(
    url: str,
    *,
    timeout_ms: int = 20_000,
    screenshot_path: str | None = None,
    user_agent: str | None = None,
    binary: str | None = None,
) -> dict[str, Any]:
    """Public entry point for the browser engine: open the remote-fetch door, then render.

    Separate from `browser_render` on purpose. `browser_render`'s gate is
    `web.playwright_enabled` -- a switch about the Playwright DEPENDENCY, off by default -- and
    this engine has no dependency to enable. Callers that want a browser under the ordinary
    `web.allow_browser_fallback` policy (the search provider does) come here; the meaning of the
    Playwright switch is left exactly as it was.
    """

    note_remote_fetch_attempt(url)
    if remote_fetch_forbidden():
        return {"status": "remote_fetch_disabled", "final_url": url}
    return _chrome_render_unchecked(
        url,
        timeout_ms=timeout_ms,
        screenshot_path=screenshot_path,
        user_agent=user_agent,
        binary=binary,
    )


def browser_render(
    url: str,
    *,
    engine: str | None = None,
    timeout_ms: int = 20_000,
    max_scroll: int = 2,
    screenshot_path: str | None = None,
) -> dict[str, Any]:
    """Render JS-heavy pages with Playwright when explicitly enabled."""

    note_remote_fetch_attempt(url)
    if remote_fetch_forbidden():
        return {"status": "remote_fetch_disabled", "final_url": url}

    env_enabled = str(os.getenv("PLAYWRIGHT_ENABLED", "")).lower()
    enabled = env_enabled in {"1", "true", "yes"} if env_enabled else policy_engine.playwright_enabled()
    if not enabled:
        return {"status": "disabled_by_policy", "final_url": url}

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        # No Playwright, and there will never be one: installing it on this machine is forbidden.
        # Drive a browser that is already here instead. Returns `missing_dependency` -- the exact
        # shape this branch used to return unconditionally -- when no browser is found, so a
        # machine without one behaves precisely as it did before.
        return _chrome_render_unchecked(
            url,
            timeout_ms=timeout_ms,
            screenshot_path=screenshot_path,
        )

    selected_engine = engine or os.getenv("BROWSER_ENGINE") or policy_engine.browser_engine()
    with sync_playwright() as playwright:
        browser = getattr(playwright, selected_engine).launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(url, wait_until="domcontentloaded")

        for _ in range(max(0, int(max_scroll))):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(600)

        html_text = page.content()
        text = page.inner_text("body") if page.locator("body").count() else ""
        final_url = page.url
        rendered_status = _classify_rendered_content(html_text, text)
        if rendered_status == "captcha":
            browser.close()
            return {"status": "captcha", "final_url": final_url}
        if rendered_status == "login_wall":
            browser.close()
            return {"status": "login_wall", "final_url": final_url}

        links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href).slice(0, 200)") or []
        if screenshot_path:
            page.screenshot(path=screenshot_path, full_page=True)
        title = page.title()
        browser.close()
        return {
            "status": "ok",
            "final_url": final_url,
            "title": title,
            "text": text[:200000],
            "html": html_text[:2000000],
            "links": links,
            "screenshot_path": screenshot_path or None,
        }
