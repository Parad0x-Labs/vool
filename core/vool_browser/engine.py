"""Engine discovery and the isolation launch flags.

Discovery mirrors `tools/browser/browser_render.py`'s law: a Chromium-family
browser already on the machine is used in place; there is no install step. The
engine ALWAYS launches with a dedicated disposable profile (never the
operator's browser profile), a mock keychain and the basic password store, so
no page, form, or credential prompt can reach the real Keychain.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Profile targets a session may NEVER point at: the operator's real browsers.
# Sabotage S2 renames this constant; the guard test must then go red.
_FORBIDDEN_PROFILE_MARKERS = (
    "library/application support/google/chrome",
    "library/application support/google/chromium",
    "library/application support/microsoft edge",
    "library/application support/mozilla",
    "library/application support/com.apple.safari",
    "library/safari",
    "library/keychains",
    ".mozilla",
    ".config/google-chrome",
    ".config/chromium",
    "safari",
)

_MACOS_APP_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Google Chrome",
)

_LINUX_PATH_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "brave-browser",
)


def refuse_real_profile_target(candidate: str) -> str | None:
    """A refusal message when *candidate* names a real browser profile/keychain
    root (or anything under the operator's home that looks like one), else None."""

    text = str(candidate or "").strip().lower()
    if not text:
        return "profile: empty profile target"
    try:
        home = str(Path.home().resolve()).lower()
    except (OSError, RuntimeError):
        home = ""
    expanded = os.path.expanduser(str(candidate)).strip().lower()
    if home and expanded.rstrip("/") == home:
        return (
            "profile: refusing the operator's home directory as a profile target; "
            "sessions only use disposable scratch profiles"
        )
    if home and (expanded == home or expanded.startswith(home + "/")):
        for marker in _FORBIDDEN_PROFILE_MARKERS:
            if marker in expanded:
                return (
                    f"profile: refusing a real browser/keychain profile target "
                    f"({marker}); sessions only use disposable scratch profiles"
                )
    for marker in _FORBIDDEN_PROFILE_MARKERS:
        if marker in text:
            return (
                f"profile: refusing a real browser/keychain profile target "
                f"({marker}); sessions only use disposable scratch profiles"
            )
    return None


#: The two flags that keep an AUTOMATED browser launch out of the operator's real
#: Keychain. On macOS, Chrome with a fresh --user-data-dir but a real password store
#: still initialises "Chrome Safe Storage", which raises a Keychain dialog per launch.
#: A disposable profile alone does NOT prevent that; these two do.
#:
#: One authority on purpose. This was defined here and honoured here, while
#: tools/browser/browser_render.py and installer/bundle/vool_window.py each built
#: their own argv with a disposable profile and neither of these flags -- so every
#: headless render and every app-window fallback could prompt. Both now import this
#: tuple rather than repeating the literals, and there is exactly one place to read to
#: know what an automated launch carries.
CREDENTIAL_ISOLATION_FLAGS: tuple[str, ...] = (
    "--use-mock-keychain",
    "--password-store=basic",
)

#: Switches that stop a launch showing first-run or default-browser UI. Automated
#: launches are unattended by definition; a prompt there is a hang nobody sees.
UNATTENDED_UI_FLAGS: tuple[str, ...] = (
    "--no-first-run",
    "--no-default-browser-check",
)


def automated_launch_flags(*, profile_dir: str) -> list[str]:
    """The floor every automated Chrome launch in this product must carry.

    Deliberately NOT the whole engine flag list: a caller with its own needs (headless
    shell, app mode, virtual time) adds to this rather than reinventing it. What it may
    not do is launch without it.
    """
    return [
        f"--user-data-dir={Path(profile_dir)}",
        *CREDENTIAL_ISOLATION_FLAGS,
        *UNATTENDED_UI_FLAGS,
    ]


def isolation_launch_flags(*, profile_dir: str) -> list[str]:
    """The launch flags every engine start carries. The guard test reads these."""

    profile = Path(profile_dir)
    return [
        f"--user-data-dir={profile}",
        # Credential isolation: a page-triggered credential save or HTTP auth
        # prompt lands in a MOCK keychain, never the operator's real one.
        *CREDENTIAL_ISOLATION_FLAGS,
        "--disable-sync",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-breakpad",
        f"--crash-dumps-dir={profile.parent / 'crash'}",
        "--mute-audio",
        "--disable-background-networking",
        "--no-service-autorun",
    ]


def find_engine_binary() -> str | None:
    """A Chromium-family browser already on this machine, or None (a legitimate
    answer: the lane then answers typed `engine_missing`, never installs)."""

    override = str(os.getenv("VOOL_BROWSER_ENGINE") or "").strip()
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        return None
    if sys.platform == "darwin":
        for candidate in _MACOS_APP_CANDIDATES:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None
    for name in _LINUX_PATH_CANDIDATES:
        located = shutil.which(name)
        if located:
            return located
    return None


__all__ = [
    "_FORBIDDEN_PROFILE_MARKERS",
    "find_engine_binary",
    "isolation_launch_flags",
    "refuse_real_profile_target",
]
