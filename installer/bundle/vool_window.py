"""VOOL native desktop window (pywebview: WebView2 on Windows, WKWebView on macOS, WebKitGTK on Linux).

Opens the VOOL chat UI (served by the local runtime on 127.0.0.1:11435) in a real native window --
its own title and taskbar/Dock entry, no browser tab strip, no address bar -- so VOOL looks like a
desktop app rather than a browser tab. On Windows, when the WebView2 runtime is absent (WebView2 is
not guaranteed on Windows 10), it falls back to the Edge --app opener BEFORE trying pywebview,
because a missing runtime does not always raise from webview.start(); this keeps a launch from
dead-ending on a blank window. On macOS/Linux there is no such runtime gate (WKWebView/WebKitGTK are
part of the OS), so pywebview is tried directly; if no native window can be shown there, a packaged
launch FAILS CLOSED (exit 1) rather than silently opening a browser and calling it success -- an
app-mode browser window is used only when VOOL_ALLOW_BROWSER_FALLBACK=1 opts in explicitly.
A single-instance guard stops repeated launches from stacking windows: a named mutex on Windows,
an exclusive flock on POSIX.

Single-lifetime ownership (2026-09-02): this host is the ONE supervisor of both the window and the
owned runtime child. A watchdog polls supervisor.assert_alive() for the whole window lifetime --
an externally killed daemon deterministically closes the window and exits the host (exit 3) instead
of leaving an empty window holding the single-instance lock. SIGTERM/SIGINT/SIGHUP run the same
teardown the normal-quit finally-block runs, then exit -- the default disposition would kill the
host without unwinding and orphan the daemon. Boot/teardown sweep vool_api.pid when it names a
provably dead pid (the owned child's pidfile after a SIGKILL; the daemon cannot clean up then).
Exit codes: 0 normal quit (and clean external termination), 1 failed launch, 3 owned runtime lost.
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

_API_ORIGIN = str(os.environ.get("VOOL_NATIVE_API_URL") or "http://127.0.0.1:11435").rstrip("/")
URL = f"{_API_ORIGIN}/chat"
SETTINGS_URL = f"{_API_ORIGIN}/settings"
SETUP_URL = f"{_API_ORIGIN}/setup"
HEALTH = f"{_API_ORIGIN}/healthz"
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# AFTER the sys.path line above, never before it. The .app launcher runs this file as a SCRIPT
# ("python .../installer/bundle/vool_window.py"), so at import time the project root is on sys.path
# only because the two lines above just put it there. An `installer.bundle` import in the header
# block raised ImportError and the window never opened -- invisible to the suite, which imports this
# as a module with the root already on the path.
from installer.bundle import pet_native

_WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
_LOCK_HANDLES: list[int] = []  # POSIX: keep the flocks held for the process lifetime
# The pre-rename app (NULLA-named builds already installed) single-instances on these SAME
# names under ITS state dir/mutex namespace. The rename must not split that guard: an old
# window and a VOOL window open at once fight over the one canonical runtime port, and the
# newer host would take it over from the older window mid-session. Both generations must
# exclude each other, so the VOOL host locks BOTH the canonical and the legacy name and
# treats EITHER being held as "a window is already open". See
# docs/VOOL_IDENTITY_COMPATIBILITY_MAP.md: the legacy lock paths are compatibility
# identifiers, frozen exactly like the bundle id.
_WIN_MUTEX_NAMES = ("Local\\VOOL_WINDOW_SINGLETON", "Local\\NULLA_WINDOW_SINGLETON")
_POSIX_LOCK_STATE_DIRS = ("VOOL", "NULLA")  # state-dir names, canonical first


def _state_dir() -> str:
    """Per-OS writable state dir for the window's log + lock."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", _HERE)
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    path = os.path.join(base, "VOOL")
    os.makedirs(path, exist_ok=True)
    return path


def _log(message: str) -> None:
    with contextlib.suppress(Exception):
        with open(os.path.join(_state_dir(), "open.log"), "a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")


def _posix_lock_base() -> str:
    """The base directory both generations' window locks live under (POSIX)."""
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    return os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")


def _single_instance() -> bool:
    """True if this is the only VOOL window; False if one is already open.

    Windows holds named mutexes for the process lifetime; POSIX holds an exclusive flock on
    a lockfile per state dir (the handles are kept in a module global so the locks live as
    long as the window). BOTH the canonical and the legacy NULLA-named lock are taken, so a
    pre-rename window and a VOOL window exclude each other instead of stacking two windows
    that fight over the one canonical runtime port (see _WIN_MUTEX_NAMES above). The legacy
    lock is CREATED even on machines that never had the old app: without holding it, an old
    NULLA.app launched after the upgrade would open beside this window.
    Both fail OPEN on unexpected errors -- only a genuinely held lock reports False.
    """
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            for name in _WIN_MUTEX_NAMES:
                kernel32.CreateMutexW(None, False, name)
                if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                    return False
            return True
        except Exception:
            return True
    try:
        import fcntl
    except Exception:
        return True  # cannot lock -> do not block the window
    acquired: list[int] = []
    for state_name in _POSIX_LOCK_STATE_DIRS:
        lock_dir = os.path.join(_posix_lock_base(), state_name)
        try:
            os.makedirs(lock_dir, exist_ok=True)
            # A raw fd (not a context-managed file) is deliberate: the flock lives exactly as
            # long as this descriptor stays open, so it must outlive this function for the
            # whole window session.
            fd = os.open(os.path.join(lock_dir, "window.lock"), os.O_CREAT | os.O_RDWR, 0o644)
        except Exception:
            continue  # this one lock is unavailable -> fail open on it (documented philosophy)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            for held in acquired:
                with contextlib.suppress(OSError):
                    os.close(held)
            return False  # held by another window -- this generation or the pre-rename one
        acquired.append(fd)
    _LOCK_HANDLES.extend(acquired)
    return True


def _has_webview2() -> bool:
    """True if the Evergreen WebView2 runtime is installed (registry), so pywebview can render."""
    try:
        import winreg
    except Exception:
        return False
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_GUID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_GUID}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_GUID}"),
    ):
        try:
            with winreg.OpenKey(hive, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
                if version and str(version) not in ("", "0.0.0.0"):
                    return True
        except OSError:
            continue
    return False


def _wait_for_server(timeout_s: float | None = None) -> bool:
    if timeout_s is None:
        # When the launcher could not even start the runtime (VOOL_RUNTIME_START_FAILED=1), polling
        # a dead port for 90 s is pointless silence: the connect error shows in a few seconds.
        timeout_s = 5.0 if os.environ.get("VOOL_RUNTIME_START_FAILED") == "1" else 90.0
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH, timeout=2) as resp:
                if getattr(resp, "status", 200) == 200:
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def _fallback_to_edge() -> None:
    """Open via the Edge --app opener when the native window is unavailable; log if even that fails."""
    ps1 = os.path.join(_HERE, "vool-open.ps1")
    if not os.path.exists(ps1):
        _log("fallback: vool-open.ps1 not found; no window opened")
        return
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", ps1],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _log("opened via Edge --app fallback")
    except Exception as exc:
        _log(f"fallback FAILED to launch Edge opener: {exc!r}")


_MAC_APP_MODE_BROWSERS = (
    ("Google Chrome", "/Applications/Google Chrome.app"),
    ("Microsoft Edge", "/Applications/Microsoft Edge.app"),
    ("Brave Browser", "/Applications/Brave Browser.app"),
    ("Chromium", "/Applications/Chromium.app"),
)


def _credential_isolation_flags() -> list[str]:
    """The keychain-isolation flags, read from the ONE authority that defines them.

    This module is deliberately stdlib-only, so the import is guarded -- but a failure
    to load them REFUSES the launch rather than proceeding without them. Copying the
    two literals here instead would create a second authority that could drift from
    `core.vool_browser.engine` silently, and the whole point of this repair is that a
    launch path which builds its own argv is a launch path that forgets.
    """
    try:
        from core.vool_browser.engine import CREDENTIAL_ISOLATION_FLAGS
    except Exception as exc:  # pragma: no cover - exercised by the guard test
        _log(f"fallback REFUSED: credential-isolation flags unavailable: {exc!r}")
        return []
    return list(CREDENTIAL_ISOLATION_FLAGS)


def _disposable_profile_flags() -> list[str]:
    """A VOOL-owned throwaway profile, the keychain-isolation flags, and the
    first-run switches.

    This fallback used to run `open -na "Google Chrome" --args --app=<url>` with
    none of the three. Chrome with no `--user-data-dir` uses the operator's DEFAULT
    profile -- their cookies, logins and live session -- and without
    `--no-first-run` / `--no-default-browser-check` it can also show first-run and
    default-browser prompts. The Windows sibling (`vool-open.ps1`) has always
    passed all three; this is the macOS/Linux lane catching up.

    Returns an empty list if no throwaway directory can be made, and the caller
    then refuses rather than launching against the real profile.
    """
    isolation = _credential_isolation_flags()
    if not isolation:
        # Fail closed. A disposable profile without these still initialises "Chrome
        # Safe Storage" against the real Keychain on macOS and prompts the operator.
        return []
    try:
        profile = tempfile.mkdtemp(prefix="vool-window-profile-")
    except OSError as exc:
        _log(f"fallback REFUSED: no disposable browser profile could be created: {exc!r}")
        return []
    return [
        f"--user-data-dir={profile}",
        *isolation,
        "--no-first-run",
        "--no-default-browser-check",
    ]


def _fallback_to_browser_posix() -> None:
    """macOS/Linux: open the chat in an app-mode browser window (no tab strip).

    Never the operator's own browser profile. If a disposable profile cannot be
    created, this refuses -- it does NOT fall through to `open <url>`, because the
    default browser opens the operator's real profile too, which is the thing being
    avoided.
    """
    isolation = _disposable_profile_flags()
    if not isolation:
        return
    if sys.platform == "darwin":
        for app_name, app_path in _MAC_APP_MODE_BROWSERS:
            user_path = os.path.expanduser(os.path.join("~", app_path.lstrip("/")))
            if os.path.isdir(app_path) or os.path.isdir(user_path):
                with contextlib.suppress(Exception):
                    subprocess.Popen(
                        ["open", "-na", app_name, "--args", f"--app={URL}", *isolation]
                    )
                    _log(f"opened via {app_name} --app fallback (disposable profile)")
                    return
        _log(
            "fallback REFUSED: no Chromium-family browser is installed, and opening the "
            "default browser would use the operator's own profile"
        )
        return
    for exe in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
        if shutil.which(exe):
            with contextlib.suppress(Exception):
                subprocess.Popen([exe, f"--app={URL}", *isolation])
                _log(f"opened via {exe} --app fallback (disposable profile)")
                return
    _log(
        "fallback REFUSED: no Chromium-family browser on PATH, and xdg-open would use "
        "the operator's own browser profile"
    )


def _browser_fallback_allowed() -> bool:
    """True only when the operator explicitly opted into the browser fallback. A packaged .app
    launch that silently opened Chrome used to exit 0 and LOOK like native success; the packaged
    native path must fail closed instead."""
    return str(os.environ.get("VOOL_ALLOW_BROWSER_FALLBACK", "")).strip().lower() in ("1", "true", "yes", "on")


def _no_native_window(reason: object) -> int:
    """macOS/Linux fail-closed handler when no real native window can be shown. Windows keeps its
    own documented WebView2-absent Edge lane (_fallback above) — that is KAS's packaging contract,
    not this one."""
    if sys.platform == "win32":
        _log(f"native window unavailable ({reason!r}); using Edge fallback")
        _fallback()
        return 0
    if _browser_fallback_allowed():
        _log(f"native window unavailable ({reason!r}); VOOL_ALLOW_BROWSER_FALLBACK set, using browser")
        _fallback()
        return 0
    _log("ERROR: native window unavailable "
         f"({reason!r}); refusing silent browser fallback (set VOOL_ALLOW_BROWSER_FALLBACK=1 to allow)")
    return 1


def _fallback() -> None:
    """Per-OS 'no native window' path: Edge opener on Windows, app-mode browser on macOS/Linux."""
    if sys.platform == "win32":
        _fallback_to_edge()
    else:
        _fallback_to_browser_posix()


# Strong refs to the delegate proxies + the pywebview delegates they forward to (NSWindow/NSApp
# delegates are weak, so these MUST stay alive) and the NSWindow we hide/show.
_ORIGINAL_WINDOW_DELEGATE = None
_ORIGINAL_APP_DELEGATE = None
_VOOL_WINDOW_DELEGATE = None
_VOOL_APP_DELEGATE = None
_DOCK_WINDOW = None


def _close_to_dock_enabled() -> bool:
    """macOS, default ON: the red close button hides the window to the Dock (the app stays live and
    its Dock icon reopens it) while Cmd+Q still quits cleanly. Escape hatch: VOOL_CLOSE_TO_DOCK=0
    restores the plain 'X quits the app' behavior. Never applies on Windows/Linux (KAS's lane UX).

    Done correctly by hooking the *window's* Cocoa close (windowShouldClose_) rather than pywebview's
    events.closing: the red X and Cmd+Q are DIFFERENT Cocoa methods, so hooking the window-level one
    hides on X WITHOUT vetoing applicationShouldTerminate_ (Cmd+Q). The earlier events.closing hook
    conflated the two and broke Quit, which is why it used to be off by default.
    """
    if sys.platform != "darwin":
        return False
    override = str(os.environ.get("VOOL_CLOSE_TO_DOCK", "")).strip().lower()
    return override not in ("0", "false", "no", "off")


def _dock_post_start(window: object):
    """Return a post-start callback (run by webview.start once the run loop is up) that installs the
    close-to-Dock delegates on the main thread, or None when disabled. Fail-soft."""
    if not _close_to_dock_enabled():
        return None

    def _post_start() -> None:
        with contextlib.suppress(Exception):
            from PyObjCTools import AppHelper

            AppHelper.callAfter(_install_dock_behavior, window)

    return _post_start


def _install_dock_behavior(window: object) -> None:
    """Main-thread: point the red X at hide-to-Dock and the Dock icon at reopen, leaving Cmd+Q to
    quit. Wraps (never replaces) pywebview's delegates by forwarding every other selector to them."""
    global _ORIGINAL_WINDOW_DELEGATE, _ORIGINAL_APP_DELEGATE
    global _VOOL_WINDOW_DELEGATE, _VOOL_APP_DELEGATE, _DOCK_WINDOW
    if _VOOL_WINDOW_DELEGATE is not None:
        return  # already installed
    try:
        import objc
        from AppKit import NSApplication
        from Foundation import NSObject
    except Exception as exc:
        _log(f"close-to-Dock: AppKit unavailable ({exc!r}); default close kept")
        return
    ns_window = getattr(window, "native", None)
    if ns_window is None:
        _log("close-to-Dock: no native NSWindow yet; default close kept")
        return
    _DOCK_WINDOW = ns_window
    app = NSApplication.sharedApplication()

    class _VoolWindowDelegate(NSObject):
        def windowShouldClose_(self, win):  # noqa: N802 (ObjC selector)
            with contextlib.suppress(Exception):
                win.orderOut_(None)  # hide; the app + its Dock icon stay alive (no last-window quit)
            _log("close button -> hidden to Dock (app stays live)")
            return False
        def respondsToSelector_(self, sel):  # noqa: N802
            if objc.super(_VoolWindowDelegate, self).respondsToSelector_(sel):
                return True
            d = _ORIGINAL_WINDOW_DELEGATE
            return bool(d is not None and d.respondsToSelector_(sel))
        def forwardingTargetForSelector_(self, sel):  # noqa: N802
            d = _ORIGINAL_WINDOW_DELEGATE
            return d if (d is not None and d.respondsToSelector_(sel)) else None

    class _VoolAppDelegate(NSObject):
        def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, has_visible):  # noqa: N802
            with contextlib.suppress(Exception):
                if _DOCK_WINDOW is not None:
                    _DOCK_WINDOW.makeKeyAndOrderFront_(None)
                    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return True
        def respondsToSelector_(self, sel):  # noqa: N802
            if objc.super(_VoolAppDelegate, self).respondsToSelector_(sel):
                return True
            d = _ORIGINAL_APP_DELEGATE
            return bool(d is not None and d.respondsToSelector_(sel))
        def forwardingTargetForSelector_(self, sel):  # noqa: N802
            d = _ORIGINAL_APP_DELEGATE
            return d if (d is not None and d.respondsToSelector_(sel)) else None

    try:
        _ORIGINAL_WINDOW_DELEGATE = ns_window.delegate()
        _VOOL_WINDOW_DELEGATE = _VoolWindowDelegate.alloc().init()
        ns_window.setDelegate_(_VOOL_WINDOW_DELEGATE)
        _ORIGINAL_APP_DELEGATE = app.delegate()
        _VOOL_APP_DELEGATE = _VoolAppDelegate.alloc().init()
        app.setDelegate_(_VOOL_APP_DELEGATE)
        _log("close-to-Dock installed (X hides, Cmd+Q quits, Dock icon reopens)")
    except Exception as exc:
        _log(f"close-to-Dock install failed ({exc!r}); default close kept")


def _settings_menu(api: object) -> list:
    """The application-menu entry for Settings, or an empty list.

    pywebview merges a Menu titled ``__app__`` into the macOS application menu right under About
    (webview/platforms/cocoa.py `_add_app_menu`), which is where macOS users look for Settings.
    The dependency is UNPINNED in the bundle build (installer/bundle/build_macos_app.sh installs
    plain `pywebview`), so the menu API may be absent in a shipped app: this returns [] there and
    the window simply starts without a menu instead of failing to start at all. The page's own
    Cmd+, handler keeps working either way.
    """
    try:
        from webview.menu import Menu, MenuAction
    except Exception as exc:
        _log(f"menu API unavailable, starting without a Settings menu ({exc!r})")
        return []

    def _open() -> None:
        with contextlib.suppress(Exception):
            api.open_settings()

    try:
        return [Menu("__app__", [MenuAction("Settings\u2026", _open)])]
    except Exception as exc:
        _log(f"Settings menu could not be built ({exc!r})")
        return []


def _bind_settings_shortcut_now() -> str:
    """One attempt to give the Settings menu item its Cmd+, key equivalent.

    Returns "bound", "missing" (the app menu has no Settings item YET) or "unavailable" (not
    macOS, no AppKit, no main menu). pywebview builds every custom MenuAction with an EMPTY key
    equivalent (cocoa.py: initWithTitle_action_keyEquivalent_(title, 'handleMenuAction:', '')),
    so the shortcut has to be set on the NSMenuItem afterwards.
    """
    if sys.platform != "darwin":
        return "unavailable"
    try:
        from AppKit import NSApplication, NSCommandKeyMask
    except Exception:
        return "unavailable"
    try:
        main_menu = NSApplication.sharedApplication().mainMenu()
        if main_menu is None or main_menu.numberOfItems() < 1:
            return "missing"
        app_menu = main_menu.itemAtIndex_(0).submenu()
        if app_menu is None:
            return "missing"
        for index in range(app_menu.numberOfItems()):
            item = app_menu.itemAtIndex_(index)
            if str(item.title()).startswith("Settings"):
                item.setKeyEquivalent_(",")
                item.setKeyEquivalentModifierMask_(NSCommandKeyMask)
                _log("Settings menu item bound to Cmd+,")
                return "bound"
        return "missing"
    except Exception as exc:
        _log(f"Cmd+, binding skipped ({exc!r})")
        return "unavailable"


def _install_settings_shortcut(attempts: int = 40, delay_s: float = 0.25) -> None:
    """Bind Cmd+, to the Settings menu item, waiting for the menu to exist.

    Binds immediately when the item is already there (the in-process tests build the menu first).
    In the packaged app the post-start hook runs BEFORE pywebview has attached the application menu
    -- measured 2026-09-06 in the built bundle: the main menu was still empty, the binder returned
    silently, and the shipped item had no key equivalent -- so a missing item is re-checked on the
    main run loop every ``delay_s`` for up to ``attempts`` tries, then logged. Best-effort: a
    non-Cocoa backend or a menu without the item leaves the menu working without the shortcut.
    """
    outcome = _bind_settings_shortcut_now()
    if outcome != "missing":
        return
    try:
        from PyObjCTools import AppHelper
    except Exception:
        _log("Settings menu item not found; no shortcut bound")
        return

    def _retry(remaining: int) -> None:
        result = _bind_settings_shortcut_now()
        if result == "missing" and remaining > 0:
            AppHelper.callLater(delay_s, _retry, remaining - 1)
        elif result == "missing":
            _log("Settings menu item not found after waiting; no shortcut bound")

    with contextlib.suppress(Exception):
        AppHelper.callLater(delay_s, _retry, attempts - 1)


def _focus_window(window: object) -> None:
    """Raise an existing pywebview window. On macOS ``show()`` already does
    makeKeyAndOrderFront_ + activateIgnoringOtherApps_, which IS focus; restore() covers a
    minimised window on the other backends."""
    with contextlib.suppress(Exception):
        window.restore()
    with contextlib.suppress(Exception):
        window.show()


# The exact keyword arguments the pixel Companion's native window is created with. Defined once,
# at module scope, so the native proof harness measures the SAME window the runtime opens instead of
# a hand-copied approximation that can drift away from production without anything failing.
COMPANION_WINDOW_FLAGS: dict = {
    "width": 176,
    "height": 176,
    "min_size": (120, 120),
    "resizable": False,
    "frameless": True,
    "easy_drag": True,
    "shadow": False,
    "focus": False,
    # NOT on_top: pywebview maps that to NSStatusWindowLevel (25), which floats above modal and
    # security panels. pet_native puts the window at NSFloatingWindowLevel (3) instead.
    "on_top": False,
    # SIX hex digits. pywebview validates background_color against ^#(?:[0-9a-fA-F]{3}){1,2}$ and an
    # eight-digit value raised ValueError before any window existed, so the pet could never detach.
    # With transparent=True the colour is applied at alpha 0, so the window is fully clear anyway.
    "background_color": "#000000",
    "transparent": True,
    "text_select": False,
    "zoomable": False,
}


class _WindowApi:
    """Bridge exposed to the VOOL page as ``window.pywebview.api`` (native window only).

    The launcher serves the page on loopback; macOS bridge messages must originate in the native
    main frame. Exposed operations are deliberately narrow: a folder picker, plus creation/update/hide
    of the presentation-only pixel Companion window, and chat/answer export saves whose destination
    must be selected in a native dialog. No page-supplied filesystem path is accepted.
    """

    def __init__(self) -> None:
        self._window: object | None = None
        self._webview: object | None = None
        self._companion_window: object | None = None
        self._pet: object | None = None
        self._settings_window: object | None = None
        self._setup_window: object | None = None

    def set_window(self, window: object, webview_module: object | None = None) -> None:
        self._window = window
        self._webview = webview_module

    def open_settings(self, section: object = None) -> dict:
        """Show VOOL Settings in its own native window, creating it once.

        A SECOND WINDOW on the SAME runtime: it loads the /settings route the API server already
        serves, so there is no second backend, no second session and no second settings store. A
        reopen focuses the window that exists rather than making another one, and closing it never
        touches the chat window, its draft, or the daemon.
        """
        try:
            webview_module = self._webview
            if webview_module is None:
                return {"ok": False, "error": "native_window_unavailable"}
            # A section deep link (#memory, #models, ...). Allowlisted to a bare identifier so
            # nothing a page can say turns this into an arbitrary navigation.
            wanted = str(section or "").strip().lstrip("#")
            if not wanted.replace("_", "").replace("-", "").isalnum():
                wanted = ""
            url = f"{SETTINGS_URL}#{wanted}" if wanted else SETTINGS_URL
            existing = self._settings_window
            if existing is not None:
                try:
                    if wanted:
                        # A window already on /settings keeps its old fragment, so point it at the
                        # asked-for section before raising it.
                        existing.load_url(url)
                    _focus_window(existing)
                    return {"ok": True, "mode": "focused"}
                except Exception:
                    self._settings_window = None   # it was really destroyed; fall through and rebuild
            created = webview_module.create_window(
                "VOOL Settings",
                url,
                js_api=self,
                width=1000,
                height=720,
                min_size=(560, 420),
            )
            if created is None:
                raise RuntimeError("native settings window was not created")
            self._settings_window = created
            # Forget the handle when the window really closes, so the NEXT open builds a fresh one
            # instead of showing a destroyed object. Deliberately hooked to `closed` and NOT to
            # `closing`: a closing handler that returns False vetoes Cmd+Q as well as the red X --
            # the exact trap documented for the main window's close-to-Dock behaviour above, which
            # is why that one is hooked at the Cocoa window level instead.
            with contextlib.suppress(Exception):
                created.events.closed += self._on_settings_closed
            _log("settings window created")
            return {"ok": True, "mode": "created"}
        except Exception as exc:
            _log(f"settings window unavailable ({exc!r})")
            return {"ok": False, "error": type(exc).__name__}

    def _on_settings_closed(self) -> None:
        """The settings window is gone; drop the handle so the next open creates one."""
        self._settings_window = None
        _log("settings window closed")

    def close_settings(self, _payload: object = None) -> dict:
        """Close the Settings window from inside the page (its Back button, or Escape).

        Closes THIS window only. The chat window, whatever is typed in it, and the runtime daemon
        are all untouched -- Settings never owned any of them.
        """
        try:
            window = self._settings_window
            if window is None:
                return {"ok": False, "error": "not_open"}
            window.destroy()
            self._settings_window = None
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}

    def open_setup(self, step: object = None) -> dict:
        """Show the guided first-run Setup in its own native window, exactly like Settings.

        A SECOND WINDOW on the SAME runtime: it loads the /setup route the API server already serves.
        A reopen focuses the window that exists; a step deep link (#step=folder) is allowlisted to a
        bare identifier so nothing a page says turns this into an arbitrary navigation. Closing it
        never touches the chat window, its draft, an open Settings window, or the daemon.
        """
        try:
            webview_module = self._webview
            if webview_module is None:
                return {"ok": False, "error": "native_window_unavailable"}
            wanted = str(step or "").strip().lstrip("#")
            if wanted.startswith("step="):
                wanted = wanted[len("step="):]
            if not wanted.replace("_", "").replace("-", "").isalnum():
                wanted = ""
            url = f"{SETUP_URL}#step={wanted}" if wanted else SETUP_URL
            existing = self._setup_window
            if existing is not None:
                try:
                    if wanted:
                        existing.load_url(url)
                    _focus_window(existing)
                    return {"ok": True, "mode": "focused"}
                except Exception:
                    self._setup_window = None
            created = webview_module.create_window(
                "VOOL Setup",
                url,
                js_api=self,
                width=920,
                height=740,
                min_size=(560, 480),
            )
            if created is None:
                raise RuntimeError("native setup window was not created")
            self._setup_window = created
            with contextlib.suppress(Exception):
                created.events.closed += self._on_setup_closed
            _log("setup window created")
            return {"ok": True, "mode": "created"}
        except Exception as exc:
            _log(f"setup window unavailable ({exc!r})")
            return {"ok": False, "error": type(exc).__name__}

    def _on_setup_closed(self) -> None:
        self._setup_window = None
        _log("setup window closed")

    def close_setup(self, _payload: object = None) -> dict:
        """Close the Setup window from inside the page ("Do this later", Escape, or the last step's Done)."""
        try:
            window = self._setup_window
            if window is None:
                return {"ok": False, "error": "not_open"}
            window.destroy()
            self._setup_window = None
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}

    def pick_folder(self) -> dict:
        """Open a native folder dialog. Returns {ok:True, path} or {ok:False, cancelled|error}."""
        try:
            window = self._window
            if window is None:
                return {"ok": False, "error": "no_window"}
            import webview

            result = window.create_file_dialog(webview.FOLDER_DIALOG)
            if not result:
                return {"ok": False, "cancelled": True}
            path = result[0] if isinstance(result, (list, tuple)) else result
            return {"ok": True, "path": str(path)}
        except Exception as exc:  # never surface a raw failure to the page
            _log(f"pick_folder failed ({exc!r})")
            return {"ok": False, "error": type(exc).__name__}

    def save_answer_pdf(self, session_id: str, request_id: str) -> dict:
        """Save one policy-gated answer through the shared native export boundary."""
        if not isinstance(request_id, str) or not 0 < len(request_id) <= 512:
            return {"ok": False, "error": "invalid_answer_identity"}
        return self._save_chat_export(session_id, "pdf", request_id=request_id)

    def save_chat_export(self, session_id: str, format_name: str,
                         timestamps: bool = False, attachments: bool = True) -> dict:
        """Save a server-owned transcript; the page chooses no URL, bytes or destination."""
        if not isinstance(timestamps, bool) or not isinstance(attachments, bool):
            return {"ok": False, "error": "invalid_export_options"}
        return self._save_chat_export(session_id, format_name,
                                      timestamps=timestamps, attachments=attachments)

    def _save_chat_export(self, session_id: str, format_name: str, *,
                          request_id: str = "", timestamps: bool = False,
                          attachments: bool = True) -> dict:
        from urllib.parse import urlencode

        if self._window is None or self._webview is None:
            return {"ok": False, "error": "no_window"}
        if not isinstance(session_id, str) or not 0 < len(session_id) <= 512:
            return {"ok": False, "error": "invalid_answer_identity" if request_id else "invalid_export_identity"}
        if not isinstance(format_name, str) or format_name not in {"md", "txt", "pdf"}:
            return {"ok": False, "error": "invalid_export_format"}
        pdf = format_name == "pdf"
        expected_type = {"md": "text/markdown", "txt": "text/plain", "pdf": "application/pdf"}[format_name]
        limit = (4 if pdf else 16) * 1024 * 1024

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            query = {"session": session_id, "format": format_name}
            if request_id:
                query["request_id"] = request_id
            else:
                query.update(timestamps="1" if timestamps else "0",
                             no_attachments="0" if attachments else "1")
            url = _API_ORIGIN + "/api/chat/export?" + urlencode(query)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(url, timeout=30) as response:
                body = response.read(limit + 1)
                if response.headers.get_content_type() != expected_type or (pdf and not body.startswith(b"%PDF-")):
                    return {"ok": False, "error": "invalid_pdf_response" if pdf else "invalid_export_response"}
            if len(body) > limit:
                return {"ok": False, "error": "pdf_too_large" if pdf else "export_too_large",
                        "message": "Export exceeds the native save size limit; no partial file was saved."}
            file_type = {"md": "Markdown documents (*.md)", "txt": "Text documents (*.txt)",
                         "pdf": "PDF documents (*.pdf)"}[format_name]
            result = self._window.create_file_dialog(self._webview.SAVE_DIALOG,
                save_filename=("vool-answer" if request_id else "vool-chat") + "." + format_name,
                file_types=(file_type,))
            if not result:
                return {"ok": False, "cancelled": True}
            destination = Path(result[0] if isinstance(result, (list, tuple)) else result)
            # Both export surfaces preserve server bytes and share the same no-clobber delivery.
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".vool-export-", delete=False) as handle:
                temporary = Path(handle.name)
                try:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                    os.link(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
            return {"ok": True}
        except urllib.error.HTTPError as exc:
            try:
                refusal = json.loads(exc.read(4096))
                message = str(refusal.get("message") or "Export refused")
            except (ValueError, AttributeError):
                message = "Export refused"
            return {"ok": False, "error": "pdf_export_refused" if pdf else "chat_export_refused", "message": message}
        except FileExistsError:
            return {"ok": False, "error": "destination_exists", "message": "Choose a new filename; the existing file was not changed."}
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}

    @staticmethod
    def _companion_payload(payload: object) -> dict[str, str]:
        """Allowlist the presentation-only values that may cross into the desktop window."""
        from core.companion_world_fragment import normalise_companion_payload

        return normalise_companion_payload(payload)

    def detach_companion(self, payload: object = None) -> dict:
        """Move the pixel companion into a real frameless native desktop window."""
        try:
            webview_module = self._webview
            if webview_module is None:
                return {"ok": False, "error": "native_window_unavailable"}
            clean = self._companion_payload(payload)
            if self._companion_window is None:
                from core.companion_world_fragment import render_desktop_companion_html

                created = webview_module.create_window(
                    "VOOL Companion",
                    html=render_desktop_companion_html(clean),
                    js_api=self,
                    **COMPANION_WINDOW_FLAGS,
                )
                if created is None:
                    raise RuntimeError("native companion window was not created")
                self._companion_window = created
                self._pet = pet_native.PetWindowController(created)
                # Persist the pet's position the moment a drag settles, not only on hide/return:
                # one drag must hold through blur/focus and restart. The controller marshals this
                # off the main thread; see PetWindowController._report_settled.
                self._pet.on_position_settled = lambda _origin: self._remember_pet_position()
                adopted = self._pet.adopt(clean.get("desktop_position"))
                _log(f"desktop companion window created (frameless, transparent, {adopted})")
            elif self._pet is not None and self._pet.available:
                self._pet.show()
            else:
                self._companion_window.show()
            self.sync_companion(clean)
            return {"ok": True, "mode": "desktop"}
        except Exception as exc:
            _log(f"desktop companion unavailable ({exc!r})")
            return {"ok": False, "error": type(exc).__name__}

    def sync_companion(self, payload: object = None) -> dict:
        """Update the detached renderer from allowlisted typed presentation state."""
        clean = self._companion_payload(payload)
        window = self._companion_window
        if window is None:
            return {"ok": False, "error": "not_detached"}
        try:
            encoded = json.dumps(clean, separators=(",", ":"), ensure_ascii=True)
            window.evaluate_js(f"window.VoolDesktopCompanion&&window.VoolDesktopCompanion.update({encoded})")
            return {"ok": True}
        except Exception as exc:
            _log(f"desktop companion sync failed ({exc!r})")
            return {"ok": False, "error": type(exc).__name__}

    def _hide_pet_window(self) -> None:
        """Order the pet out through whichever layer actually owns a window on this platform."""
        if self._pet is not None and self._pet.available:
            self._pet.hide()
        elif self._companion_window is not None:
            self._companion_window.hide()

    def pet_yield_rect(self, payload: object = None) -> dict:
        """Hand the pet the OPERATOR-ANSWER region it must not intercept pointer clicks over.

        Called by the chat page while a permission bar or mode banner is visible (payload: the
        footer's page screen rect, padded), and with null when the surface clears. Presentation
        only -- pointer routing -- and deliberately one narrow shape: a rect, validated and
        converted by the pet controller, never an arbitrary window or event handed to the page.
        """
        try:
            if self._pet is None:
                return {"ok": False, "error": "pet_not_active"}
            return self._pet.set_yield_rect(payload)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}

    def _remember_pet_position(self) -> None:
        """Hand the pet's native position to the page, which persists it in the ONE existing
        companion preference (``vool_ninja_pos_v1``). No second preference store is created."""
        if self._pet is None or self._window is None:
            return
        position = self._pet.position()
        if not position:
            return
        payload = json.dumps(position, separators=(",", ":"), ensure_ascii=True)
        with contextlib.suppress(Exception):
            self._window.evaluate_js(
                "window.VoolCompanion&&window.VoolCompanion.desktopMoved&&"
                f"window.VoolCompanion.desktopMoved({payload})"
            )

    def hide_companion(self, _payload: object = None) -> dict:
        """Hide the pet from its own control. Chat, the daemon and the main window are untouched."""
        try:
            self._remember_pet_position()
            self._hide_pet_window()
            if self._window is not None:
                with contextlib.suppress(Exception):
                    self._window.evaluate_js(
                        "window.VoolCompanion&&window.VoolCompanion.desktopHidden&&"
                        "window.VoolCompanion.desktopHidden()"
                    )
            _log("desktop companion hidden")
            return {"ok": True, "mode": "hidden"}
        except Exception as exc:
            _log(f"desktop companion hide failed ({exc!r})")
            return {"ok": False, "error": type(exc).__name__}

    def reset_companion_position(self, _payload: object = None) -> dict:
        """Put a lost pet back on the primary display."""
        try:
            if self._pet is None:
                return {"ok": False, "error": "not_detached"}
            position = self._pet.reset_position()
            self._remember_pet_position()
            return {"ok": True, "position": position}
        except Exception as exc:
            _log(f"desktop companion reset failed ({exc!r})")
            return {"ok": False, "error": type(exc).__name__}

    def companion_overhead(self, _payload: object = None) -> dict:
        """Observed cost of the pet's cursor tracking. Measured, never assumed to be free."""
        return self._pet.stats() if self._pet is not None else {"running": False, "ticks": 0}

    def open_companion_appearance(self) -> dict:
        """Open fixed presentation controls in their owning chat window."""
        if self._window is None:
            return {"ok": False, "error": "chat_unavailable"}
        try:
            opened = self._window.evaluate_js(
                "Boolean(window.VoolCompanionDrawer && window.VoolCompanionDrawer.open())"
            )
            if opened is not True:
                return {"ok": False, "error": "appearance_unavailable"}
            _focus_window(self._window)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}

    def attach_companion(self) -> dict:
        """Hide the native pet window and return the same renderer to the main app."""
        try:
            self._remember_pet_position()
            self._hide_pet_window()
            if self._window is not None:
                self._window.evaluate_js(
                    "window.VoolCompanion&&window.VoolCompanion.desktopReturned&&window.VoolCompanion.desktopReturned()"
                )
            _log("desktop companion returned to the VOOL window")
            return {"ok": True, "mode": "app"}
        except Exception as exc:
            _log(f"desktop companion attach failed ({exc!r})")
            return {"ok": False, "error": type(exc).__name__}


_URL_SCHEME_HANDLER = None  # keep a strong ref so the Apple Event handler is not GC'd


def _four_char_code(code: str) -> int:
    return (ord(code[0]) << 24) | (ord(code[1]) << 16) | (ord(code[2]) << 8) | ord(code[3])


def _deliver_oauth_url(url: str) -> None:
    """Parse vool://auth/<provider>/callback?code=&state= and POST {code,state} to the local runtime.

    The desktop owns state-verification + the code->key exchange; this only relays the params the
    provider handed us to the runtime, over loopback. Fail-soft: any error is logged, never raised.
    """
    import json
    import urllib.parse

    if not str(url or "").lower().startswith("vool://"):
        return
    parsed = urllib.parse.urlparse(url)
    segments = [seg for seg in (parsed.netloc + parsed.path).split("/") if seg]
    provider = ""
    if len(segments) >= 3 and segments[0] == "auth" and segments[-1] == "callback":
        provider = segments[1].lower()
    query = urllib.parse.parse_qs(parsed.query)
    code = (query.get("code") or [""])[0]
    state = (query.get("state") or [""])[0]
    if not (provider and code and state):
        _log("vool:// callback missing provider/code/state; ignored")
        return
    payload = json.dumps({"code": code, "state": state}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:11435/api/auth/{provider}/callback",
        data=payload,
        headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:11435"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=5).read()
        _log(f"delivered vool:// {provider} oauth callback (state …{state[-6:]})")
    except Exception as exc:
        _log(f"failed to deliver vool:// oauth callback ({exc!r})")


def _register_url_scheme_handler() -> None:
    """macOS: catch the vool:// launch/activation (GURL Apple Event) and relay its params.

    The .app's bash launcher exec's into THIS process, so the window is the .app's NSApplication and
    receives the Apple Event LaunchServices posts for a registered URL scheme. Registered before the
    pywebview run loop starts, so a URL that launched the app (queued by the OS) is delivered once the
    loop runs. macOS only; fail-soft.
    """
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSAppleEventManager, NSObject
    except Exception as exc:
        _log(f"vool:// scheme handler unavailable ({exc!r})")
        return

    key_direct_object = _four_char_code("----")

    class _VoolURLHandler(NSObject):
        def handleAppleEvent_withReplyEvent_(self, event, reply_event):  # noqa: N802 (ObjC selector)
            try:
                descriptor = event.paramDescriptorForKeyword_(key_direct_object)
                _deliver_oauth_url(str(descriptor.stringValue() or "")) if descriptor else None
            except Exception as exc:
                _log(f"vool:// handler error ({exc!r})")

    global _URL_SCHEME_HANDLER
    try:
        _URL_SCHEME_HANDLER = _VoolURLHandler.alloc().init()
        manager = NSAppleEventManager.sharedAppleEventManager()
        get_url = _four_char_code("GURL")  # kInternetEventClass == kAEGetURL == 'GURL'
        manager.setEventHandler_andSelector_forEventClass_andEventID_(
            _URL_SCHEME_HANDLER, b"handleAppleEvent:withReplyEvent:", get_url, get_url,
        )
        _log("vool:// URL scheme handler registered")
    except Exception as exc:
        _log(f"could not register vool:// scheme handler ({exc!r})")


def _claim_macos_app_name() -> None:
    """macOS: make the menu bar, Cmd-Tab and Activity Monitor say 'VOOL'.

    The bundle is VOOL.app (CFBundleExecutable + folder frozen per the migration), so without this
    the app switcher can fall back to the executable name 'VOOL' (or 'Python'). Cocoa reads the menu
    app name from the main bundle's CFBundleName at app-init, so we overwrite that in-memory before
    pywebview builds the NSApplication. Also set the POSIX process name. macOS only; fail-soft.
    """
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle, NSProcessInfo
        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "VOOL"
        NSProcessInfo.processInfo().setProcessName_("VOOL")
        _log("claimed macOS app name VOOL")
    except Exception as exc:
        _log(f"could not claim macOS app name ({exc!r})")


def _runtime_pidfile_path() -> Path | None:
    """Where the daemon writes its pid (apps/vool_api_server: active_data_dir()/vool_api.pid).
    Same VOOL_HOME resolution the child inherits, so both agree on the one path."""
    try:
        from core.runtime_paths import active_data_dir

        return Path(active_data_dir()) / "vool_api.pid"
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists but not ours to signal
    return True


def _sweep_owned_runtime_pidfile(owned_pid: int | None) -> None:
    """Remove vool_api.pid when it names THIS window's own child. A SIGKILLed daemon never runs
    its cleanup, so the owner that spawned (and outlived) it must sweep. A pidfile naming any
    other process is never touched."""
    if owned_pid is None:
        return
    path = _runtime_pidfile_path()
    if path is None:
        return
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if content == str(int(owned_pid)):
        with contextlib.suppress(OSError):
            path.unlink()
        _log(f"swept owned runtime pidfile (child pid {owned_pid})")


def _sweep_stale_runtime_pidfile() -> None:
    """Boot-time hygiene: remove a leftover pidfile only when it names a PROVABLY dead pid. A
    live occupant's pidfile is load-bearing (the self-updater stops that exact process) and stays."""
    path = _runtime_pidfile_path()
    if path is None:
        return
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if content.isdigit():
        pid = int(content)
        if pid > 1 and not _pid_alive(pid):
            with contextlib.suppress(OSError):
                path.unlink()
            _log(f"swept stale runtime pidfile naming dead pid {pid}")


def _teardown_owned_runtime_and_exit(supervisor: object, owned_pid: dict[str, int | None], reason: str) -> None:
    """The one teardown a termination signal runs: release the owned runtime, sweep, exit.

    Shared by the Windows handler and the POSIX signal-watcher thread so there is exactly one
    implementation of the contract, not two that can drift.
    """
    _log(reason)
    with contextlib.suppress(Exception):
        if supervisor is not None:
            supervisor.shutdown()
    with contextlib.suppress(Exception):
        _sweep_owned_runtime_pidfile(owned_pid.get("pid"))
    # A signal that lands before ensure_ready() returns has no captured owned pid, and a
    # daemon SIGKILLed mid-graceful-shutdown never runs its own cleanup — the stale sweep
    # (provably dead pid) is the fallback that keeps the state dir clean.
    with contextlib.suppress(Exception):
        _sweep_stale_runtime_pidfile()
    _log("owned runtime released on signal")
    os._exit(0)


def _install_termination_handlers(supervisor: object, owned_pid: dict[str, int | None]) -> None:
    """SIGTERM/SIGINT/SIGHUP run the same teardown the normal-quit finally-block runs, then exit.
    The default disposition kills this host without unwinding, which is exactly how an external
    SIGTERM used to orphan the owned daemon and its 11435 listener.

    POSIX (measured 2026-09-19 on the packaged app): a Python-level handler NEVER RUNS while
    the main thread is parked in webview.start()'s native run loop (NSApp.run and friends
    never return to Python bytecode), so `kill -TERM <host>` was ignored outright — host,
    owned daemon and the port all stayed up indefinitely. The teardown therefore runs on a
    watcher thread woken by signal.set_wakeup_fd: CPython's C-level trampoline writes one
    byte per caught signal to the pipe without needing the main thread, and the watcher does
    the shutdown + sweeps + exit. The registered Python handlers stay as no-op markers so
    the default disposition (death without unwinding) never applies. Windows keeps the
    direct handler: its signal delivery is not blocked by a native run loop in the same way,
    and that lane is not re-tested here.
    """
    if os.name != "posix":
        def _terminate(signum: int, _frame: object) -> None:
            _teardown_owned_runtime_and_exit(
                supervisor, owned_pid, f"{signal.Signals(signum).name} received; tearing down the owned runtime"
            )

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with contextlib.suppress(Exception):
                signal.signal(sig, _terminate)
        return

    read_fd, write_fd = os.pipe()
    # The WRITE end must be non-blocking (CPython's signal trampoline writes to it from
    # async-signal context and must never stall). The READ end stays BLOCKING so the
    # watcher's os.read sleeps until a byte arrives — a non-blocking read on an empty pipe
    # raises BlockingIOError (an OSError), which this watcher must not treat as fatal.
    os.set_blocking(write_fd, False)
    try:
        signal.set_wakeup_fd(write_fd)
    except (OSError, ValueError) as exc:
        _log(f"signal wakeup pipe unavailable ({exc!r}); falling back to in-thread handlers")
        with contextlib.suppress(OSError):
            os.close(read_fd)
        with contextlib.suppress(OSError):
            os.close(write_fd)

        def _fallback_terminate(signum: int, _frame: object) -> None:
            _teardown_owned_runtime_and_exit(
                supervisor, owned_pid, f"{signal.Signals(signum).name} received; tearing down the owned runtime"
            )

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with contextlib.suppress(Exception):
                signal.signal(sig, _fallback_terminate)
        return

    def _mark(signum: int, _frame: object) -> None:
        # The real work happens on the watcher thread; this handler exists only so CPython
        # catches the signal (and writes the wakeup byte) instead of applying the default
        # disposition. It must stay trivially cheap — some delivery contexts call it with
        # locks held.
        return

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        with contextlib.suppress(Exception):
            signal.signal(sig, _mark)

    def _watch() -> None:
        while True:
            try:
                woke = os.read(read_fd, 16)
            except InterruptedError:
                continue
            except OSError:
                return
            if not woke:
                return  # write end closed (process teardown) — nothing more to watch
            _teardown_owned_runtime_and_exit(
                supervisor, owned_pid,
                "termination signal received (SIGTERM/SIGINT/SIGHUP) while the native run "
                "loop owned the main thread; tearing down the owned runtime",
            )

    threading.Thread(target=_watch, name="nulla-signal-watcher", daemon=True).start()


def _start_runtime_watchdog(supervisor: object, window: object, done: threading.Event) -> threading.Event:
    """Poll supervisor.assert_alive() for the whole window lifetime. The 2026-09-02 defect: the
    contract existed but nothing called it, so an externally killed daemon left the window host
    alive forever. On loss: log, close the window, and if the event loop has not unwound within
    20s, force exit 3 — the host never outlives its runtime by more than a bounded grace.
    Returns the `lost` event; main() exits 3 when it is set. Supervisors without the contract
    (test stand-ins) get no watchdog rather than a broken one."""
    lost = threading.Event()
    check = getattr(supervisor, "assert_alive", None)
    if not callable(check):
        return lost
    try:
        poll = max(0.05, float(os.environ.get("VOOL_RUNTIME_WATCHDOG_POLL") or "2.0"))
    except ValueError:
        poll = 2.0

    def _watch() -> None:
        while not lost.wait(poll):
            try:
                check()
            except Exception as exc:
                _log(f"runtime lost ({exc}); closing the window deterministically")
                lost.set()
                with contextlib.suppress(Exception):
                    window.destroy()
                if not done.wait(20.0):
                    _log("window did not close after runtime loss; forcing exit")
                    os._exit(3)
                return

    threading.Thread(target=_watch, name="vool-runtime-watchdog", daemon=True).start()
    return lost


def _install_native_frame_authority() -> None:
    if sys.platform == 'darwin':
        from installer.bundle.native_frame_authority import install_macos_frame_authority

        install_macos_frame_authority()


def _start_notification_bridge(window: object) -> object | None:
    """macOS notifications: run the notification helper for this host's lifetime (installer/bundle/native_notifications.py).
    A notification the person clicks brings this window forward and opens the matching bell item. Fail-soft: the bell
    keeps every alert whether or not the helper runs."""
    if sys.platform != "darwin":
        return None
    try:
        from installer.bundle.native_notifications import start_for_host
    except Exception as exc:
        _log(f"notification bridge unavailable ({exc!r})")
        return None

    def _open(entry: dict) -> None:
        _focus_window(window)
        with contextlib.suppress(Exception):
            item_id = json.dumps(str(entry.get("notification_id") or ""))
            window.evaluate_js(f"window.VoolNotify && window.VoolNotify.openItem && window.VoolNotify.openItem({item_id})")

    try:
        return start_for_host(api_origin=_API_ORIGIN, on_open=_open, log=_log)
    except Exception as exc:
        _log(f"notification bridge did not start ({exc!r})")
        return None


def main() -> int:
    supervisor = None
    notification_bridge = None
    owned_pid: dict[str, int | None] = {"pid": None}
    done = threading.Event()
    try:
        if not _single_instance():
            _log("another VOOL window is already open; not opening a second")
            return 0

        # A pidfile naming a provably dead daemon is leftover garbage from a SIGKILL; clear it
        # before the identity gate so nothing mistakes it for a live occupant.
        _sweep_stale_runtime_pidfile()

        # Runtime ownership belongs to this long-lived native host, not to a short-lived shell
        # launcher. No window is created until the exact source identity answers /healthz.
        from installer.bundle.native_runtime_supervisor import NativeRuntimeError, NativeRuntimeSupervisor

        # Orphan defense for the child we are about to spawn: the daemon watches this pid and
        # exits when it dies (macOS has no PR_SET_PDEATHSIG). Armed only for the owned path --
        # an attached/foreign runtime is someone else's process tree.
        require_owned = str(os.environ.get("VOOL_NATIVE_REQUIRE_OWNED_RUNTIME") or "1").strip().lower() not in {
            "0", "false", "no", "off",
        }
        if require_owned:
            os.environ["VOOL_OWNED_BY_WINDOW_PID"] = str(os.getpid())

        supervisor = NativeRuntimeSupervisor.from_environment(module_path=__file__)
        _install_termination_handlers(supervisor, owned_pid)
        try:
            identity = supervisor.ensure_ready()
        except NativeRuntimeError as exc:
            _log(f"ERROR: exact native runtime unavailable ({exc})")
            return 1
        owned_child = getattr(supervisor, "process", None)
        if supervisor.owns_runtime and owned_child is not None:
            owned_pid["pid"] = int(owned_child.pid)
        _log(
            "runtime ready "
            f"commit={identity.get('commit_full') or identity.get('commit') or 'unknown'} "
            f"pid={identity.get('pid') or 'unknown'} "
            f"ownership={'native-host' if supervisor.owns_runtime else 'matching-existing'}"
        )

        # The WebView2 runtime gate is Windows-only; macOS (WKWebView) and Linux (WebKitGTK) ship a
        # web view with the OS, so pywebview is tried directly there.
        if sys.platform == "win32" and not _has_webview2():
            _log("WebView2 runtime not found; using Edge fallback")
            _fallback_to_edge()
            return 0
        try:
            import webview  # pywebview -> WebView2 (Windows) / WKWebView (macOS) / WebKitGTK (Linux)
        except Exception as exc:
            _log(f"pywebview import failed ({exc!r})")
            return _no_native_window(exc)

        api = _WindowApi()
        _claim_macos_app_name()          # so the menu bar / Cmd-Tab say VOOL, not VOOL/Python
        _install_native_frame_authority()
        window = webview.create_window("VOOL", URL, js_api=api, width=1220, height=860, min_size=(900, 640))
        if window is None:
            raise RuntimeError("pywebview returned no native window")
        api.set_window(window, webview)
        _register_url_scheme_handler()   # catch vool://auth/<provider>/callback launches (macOS)
        runtime_lost = _start_runtime_watchdog(supervisor, window, done)
        notification_bridge = _start_notification_bridge(window)
        # macOS: X hides to Dock, Cmd+Q quits (default on); the same post-start hook binds Cmd+,
        # to the Settings item, because pywebview creates custom menu items with no shortcut.
        _post_start = _dock_post_start(window)

        def _post_start_all() -> None:
            _post_start()
            try:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(_install_settings_shortcut)   # main thread, like the dock hook
            except Exception:
                _install_settings_shortcut()

        menu = _settings_menu(api)
        try:
            webview.start(_post_start_all, menu=menu)
        except TypeError:
            # An older pywebview without the `menu` keyword: start the window anyway. Settings
            # stays reachable from the chat sidebar and from Cmd+, inside the page; only the menu
            # bar entry is missing. Never trade the whole window for the menu.
            _log("pywebview does not accept a menu; starting without the Settings menu item")
            webview.start(_post_start_all)
        _log("native window closed")
        return 3 if runtime_lost.is_set() else 0
    except Exception as exc:
        _log(f"native window failed ({exc!r})")
        return _no_native_window(exc)
    finally:
        if notification_bridge is not None:
            with contextlib.suppress(Exception):
                notification_bridge.stop()
        if supervisor is not None:
            with contextlib.suppress(Exception):
                supervisor.shutdown()
            with contextlib.suppress(Exception):
                _sweep_owned_runtime_pidfile(owned_pid.get("pid"))
            with contextlib.suppress(Exception):
                _sweep_stale_runtime_pidfile()
            _log("native runtime ownership released")
        done.set()
        for held in list(_LOCK_HANDLES):
            with contextlib.suppress(Exception):
                os.close(held)
        _LOCK_HANDLES.clear()


if __name__ == "__main__":
    sys.exit(main())
