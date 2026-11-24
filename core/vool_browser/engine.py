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


#: The engine's own services that would contact a server without the person asking.
_QUIET_FEATURES = (
    "MediaRouter", "DialMediaRouteProvider", "OptimizationHints", "OptimizationHintsFetching",
    "OptimizationGuideModelDownloading", "NetworkTimeServiceQuerying",
    "InterestFeedContentSuggestions", "CertificateTransparencyComponentUpdater",
    "AutofillServerCommunication", "Translate", "PreconnectToSearch", "ZeroSuggestPrefetching",
    "ChromeWhatsNewUI",
)

#: An address on this machine where nothing listens. The engine's built-in account,
#: push-message and component services are pointed here, so they cannot reach their
#: makers' servers. Signing in to a website is ordinary browsing and is not affected.
_NOWHERE = "http://127.0.0.1:9"
#: The one name the engine's maker serves its settings-reset list from.
MAKER_UPDATE_HOST = "tools.google.com"

#: Quiet by default: page requests the person makes are the only traffic. Measured on
#: the real engine through a recording relay (tests/test_desk_browser.py); each line
#: below removed a background contact that was otherwise made within seconds of start.
QUIET_FLAGS: tuple[str, ...] = (
    "--disable-sync",
    "--disable-background-networking",
    "--disable-component-update",
    f"--component-updater=url-source={_NOWHERE}/",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--disable-breakpad",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
    "--disable-field-trial-config",
    "--no-experiments",
    "--no-pings",
    "--no-service-autorun",
    "--metrics-recording-only",
    f"--gaia-url={_NOWHERE}",
    f"--gcm-checkin-url={_NOWHERE}",
    f"--gcm-registration-url={_NOWHERE}",
    "--gcm-mcs-endpoint=127.0.0.1:9",
    # An engine that carries its maker's brand asks that maker's update address for a
    # list each time its settings page is opened, and has no switch for it. The name is
    # pointed at the address where nothing listens. The engine applies this on its direct
    # way out only: a route VOOL sets keeps the name off itself (see ``desk``), and a relay
    # set in the computer's own network settings is still asked for it. A page on that
    # one name cannot be opened in this browser either.
    f"--host-rules=MAP {MAKER_UPDATE_HOST} 127.0.0.1:9",
    "--disable-features=" + ",".join(_QUIET_FEATURES),
)
#: Used by the everyday window and by the hidden engine that does VOOL's own web lookups: the
#: lookup engine, measured the same way without them, asked for accounts.google.com and the
#: engine maker's download server on every single lookup.


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
    already = {"--disable-sync", "--disable-breakpad", "--disable-background-networking", "--no-service-autorun"}
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
        # The engine's own sign-in, push and update services are pointed nowhere. Measured
        # without these: every session start asked for the engine maker's sign-in and
        # push-message servers before any page was opened. The feature switch is left to the
        # driver that starts the engine: it passes one of its own, and the engine reads only
        # the last one it is given.
        *(flag for flag in QUIET_FLAGS if flag not in already and not flag.startswith("--disable-features=")),
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
    "MAKER_UPDATE_HOST",
    "QUIET_FLAGS",
    "_FORBIDDEN_PROFILE_MARKERS",
    "find_engine_binary",
    "isolation_launch_flags",
    "refuse_real_profile_target",
]
