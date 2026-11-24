"""Engine discovery and the isolation launch flags.

Discovery mirrors `tools/browser/browser_render.py`'s law: a Chromium-family
browser already on the machine is used in place; there is no install step. The
engine ALWAYS launches with a dedicated disposable profile (never the
operator's browser profile), a mock keychain and the basic password store, so
no page, form, or credential prompt can reach the real Keychain.
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import sys
from pathlib import Path
from typing import Any

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

#: The browsers VOOL runs on, on a Mac, by the name of the app bundle, in order of preference.
#: The program inside each bundle is read from the bundle's own Info.plist
#: (CFBundleExecutable), never typed here.
#:
#: Only browsers whose own background contacts were measured and closed are named. Brave and
#: Microsoft Edge are Chromium-family too, and are left out on purpose: measured on their
#: Linux builds in November 2026, with every setting below in force, Brave reported its use to
#: its maker at each start, gave a site embedded in another site its cookie and fetched site
#: icons by itself; Edge contacted its maker's configuration, search and address-checking
#: services in the window, in VOOL's own lookups and through a Private web route. A computer
#: that has only one of those has no engine for VOOL until each is measured and closed as
#: this one was.
MACOS_APP_NAMES = ("Google Chrome", "Chromium")

#: Where a Mac keeps apps: the shared folder, then the person's own.
MACOS_APP_FOLDERS = ("/Applications", "~/Applications")

#: The same browsers on Linux, by program name, looked up on PATH in this order.
LINUX_PROGRAM_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
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
    # An engine whose search is its maker's asks that company at every start whether its
    # "AI Mode" may be offered. Only the request at start is switched off.
    "AimServerRequestOnStartupEnabled",
)

#: An address on this machine where nothing listens. The engine's built-in account,
#: push-message and component services are pointed here, so they cannot reach their
#: makers' servers. Signing in to a website is ordinary browsing and is not affected.
_NOWHERE = "http://127.0.0.1:9"
#: Names of the engine maker's update service that the engine asks by itself and has no
#: switch for. Measured on a branded engine: the first each time its settings page is
#: opened (a list for "reset settings"), the second each time its own private window is
#: opened. VOOL has the engine's updates of its own parts switched off as it is.
MAKER_UPDATE_HOSTS = ("tools.google.com", "update.googleapis.com")

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
    # The maker's update names (see above) are pointed at the address where nothing
    # listens. The engine applies this on its direct way out only: a route VOOL sets keeps
    # the names off itself (see ``desk``), and a relay set in the computer's own network
    # settings is still asked for them. A page on those names cannot be opened in this
    # browser either.
    "--host-rules=" + ", ".join(f"MAP {name} 127.0.0.1:9" for name in MAKER_UPDATE_HOSTS),
    "--disable-features=" + ",".join(_QUIET_FEATURES),
)
#: Used by the everyday window and by the hidden engine that does VOOL's own web lookups: the
#: lookup engine, measured the same way without them, asked for accounts.google.com and the
#: engine maker's download server on every single lookup.

#: Spell-check in a new profile. On macOS the engine uses the system's own checker and
#: fetches nothing, so it stays on. Elsewhere the engine has no dictionary of its own:
#: measured, it downloads one from its maker's server the first time something is typed in
#: a page, and switching the check off does not stop that while a language is still chosen
#: for it. Nothing is fetched that the person did not ask for, so there a new profile starts
#: with the check off and no language chosen for it; the person switches it on and picks the
#: language in the browser's settings, and the engine fetches the dictionary then.
_SYSTEM_SPELL_CHECK = sys.platform == "darwin"


def quiet_preferences() -> dict[str, Any]:
    """The settings half of quiet: what only a profile's own settings switch off.

    The launch options above cannot reach these. Measured on a branded engine with a new
    profile and every launch option in force: its safe-browsing service fetched a key from
    its maker's servers within seconds of starting, which it needs to ask those servers
    about each address opened, and its spell-check downloaded a dictionary. With that
    service off the engine asks its maker about no address; the lists of harmful sites
    that warn in VOOL's browser are VOOL's own (``site_lists``).
    """
    quiet: dict[str, Any] = {
        "search": {"suggest_enabled": False},
        "alternate_error_pages": {"enabled": False},
        "net": {"network_prediction_options": 2},   # no speculative connections
        "safebrowsing": {"enabled": False, "enhanced": False, "scout_reporting_enabled": False},
        "signin": {"allowed": False},
        "privacy_sandbox": {"m1": {"topics_enabled": False, "fledge_enabled": False, "ad_measurement_enabled": False}},
    }
    if not _SYSTEM_SPELL_CHECK:
        quiet["browser"] = {"enable_spellchecking": False}
        # Two settings name the languages. The engine carries the older, single one
        # ("dictionary", which it holds as its own language until a first start has emptied
        # it) over into an empty list, so both are written empty.
        quiet["spellcheck"] = {"dictionaries": [], "dictionary": ""}
    return quiet


def start_quiet(profile_dir: str | Path) -> bool:
    """Give a profile the engine has never opened the quiet settings. True when written.

    For the hidden engines, whose throwaway profiles would otherwise begin with the
    engine's own settings. A profile that already has settings is left as it is.
    """
    target = Path(profile_dir) / "Default" / "Preferences"
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text(json.dumps(quiet_preferences()), encoding="utf-8")
    return True


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


def macos_app_program(app: Path) -> str | None:
    """The runnable program inside a Mac app bundle, named by its Info.plist, or None."""

    name = app.stem
    try:
        with open(app / "Contents" / "Info.plist", "rb") as handle:
            declared = plistlib.load(handle).get("CFBundleExecutable")
        if isinstance(declared, str) and declared.strip() and "/" not in declared:
            name = declared.strip()
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    program = app / "Contents" / "MacOS" / name
    return str(program) if program.is_file() and os.access(program, os.X_OK) else None


def macos_app_programs(folders=None, names=None) -> list[str]:
    """Every installed Chromium-family program on a Mac, in ``names`` order then folder order."""

    found = []
    for name in MACOS_APP_NAMES if names is None else names:
        for folder in MACOS_APP_FOLDERS if folders is None else folders:
            program = macos_app_program(Path(os.path.expanduser(folder)) / f"{name}.app")
            if program:
                found.append(program)
    return found


def find_engine_binary() -> str | None:
    """A Chromium-family browser already on this machine, or None (a legitimate
    answer: the lane then answers typed `engine_missing`, never installs)."""

    override = str(os.getenv("VOOL_BROWSER_ENGINE") or "").strip()
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        return None
    if sys.platform == "darwin":
        return next(iter(macos_app_programs()), None)
    for name in LINUX_PROGRAM_NAMES:
        located = shutil.which(name)
        if located:
            return located
    return None


__all__ = [
    "LINUX_PROGRAM_NAMES",
    "MACOS_APP_FOLDERS",
    "MACOS_APP_NAMES",
    "MAKER_UPDATE_HOSTS",
    "QUIET_FLAGS",
    "_FORBIDDEN_PROFILE_MARKERS",
    "find_engine_binary",
    "isolation_launch_flags",
    "macos_app_program",
    "macos_app_programs",
    "quiet_preferences",
    "refuse_real_profile_target",
    "start_quiet",
]
