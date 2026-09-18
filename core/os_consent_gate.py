"""OS-native user-consent gate for sensitive wallet actions.

The agent's Solana private seed is encrypted at rest and used transiently for
signing. Revealing the raw key to a human (for backup) is a different, higher-bar
action: it must require a live, OS-level confirmation so that neither OpenClaw nor
the agent process can surface the key silently.

Design goals:
  * Fail CLOSED. If no OS consent mechanism is available, consent is DENIED -
    never silently granted.
  * Prefer real verification on every OS, then fall back to a presence prompt:
      - Windows: Windows Hello (WinRT UserConsentVerifier) -> CredUI presence dialog.
      - macOS:   LocalAuthentication (Touch ID / login password) -> osascript dialog.
      - Linux:   polkit `pkexec` password auth -> zenity/kdialog presence dialog.
    The real-verification tier authenticates the user; the presence tier only proves
    a live human actively confirmed - weaker, documented as such, not oversold.
  * Be fully unit-testable without a live prompt via `set_consent_override_for_tests`.

A single explicit escape hatch exists for headless/CI: VOOL_WALLET_SKIP_CONSENT_GATE=yes.
It requires the literal value "yes", logs a warning every time, and must never be
set in a normal user install.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Callable

logger = logging.getLogger("vool.consent")

_SKIP_ENV = "VOOL_WALLET_SKIP_CONSENT_GATE"
# Neutral consent title used for EVERY OS prompt. It was hardcoded to "VOOL wallet ..." even for
# non-wallet actions like a file move, so a user habituated to approving "wallet" prompts could
# approve an unrelated action. The prompt BODY always states the specific action being consented.
_CONSENT_TITLE = "VOOL - confirm it's you"

# Test-only injection point. When set, it fully replaces the native path so unit
# tests can simulate grant/deny without a real OS prompt.
_TEST_OVERRIDE: Callable[[str], bool] | None = None


class ConsentDeniedError(Exception):
    """The user was prompted and declined / failed verification."""


class ConsentUnavailableError(Exception):
    """No OS consent mechanism is available on this platform/config (fail closed)."""


def set_consent_override_for_tests(fn: Callable[[str], bool] | None) -> None:
    """Install (or clear with None) a test hook that stands in for the OS prompt."""
    global _TEST_OVERRIDE
    _TEST_OVERRIDE = fn


def _env_bypass_enabled() -> bool:
    return str(os.environ.get(_SKIP_ENV, "")).strip().lower() == "yes"


def require_os_user_consent(reason: str) -> bool:
    """Return True only if the OS confirmed a live user consent for `reason`.

    Raises ConsentDeniedError if the user declined, ConsentUnavailableError if no mechanism
    exists. Callers gating a key reveal should treat any exception as "do not reveal".
    """
    clean_reason = str(reason or "confirm this sensitive action").strip()

    if _TEST_OVERRIDE is not None:
        return bool(_TEST_OVERRIDE(clean_reason))

    if _env_bypass_enabled():
        logger.warning(
            "OS consent gate BYPASSED via %s=yes for: %s. This must never be set on a real install.",
            _SKIP_ENV,
            clean_reason,
        )
        return True

    if sys.platform == "win32":
        return _consent_windows(clean_reason)
    if sys.platform == "darwin":
        return _consent_macos(clean_reason)
    if sys.platform.startswith("linux"):
        return _consent_linux(clean_reason)
    raise ConsentUnavailableError(
        "OS consent gate is not implemented on this platform; refusing to proceed without a live prompt"
    )


def _resolve_tier(
    verify_fn: Callable[[], bool | None],
    present_fn: Callable[[], bool | None],
    *,
    mechanism: str,
) -> bool:
    """Shared verify->presence resolution: real auth wins; else a presence prompt; else fail closed.

    The tiers are thunks so the presence prompt only fires when real verification is unavailable --
    never both (a value-eager version would pop two dialogs).
    """
    verify = verify_fn()
    if verify is not None:
        if not verify:
            raise ConsentDeniedError(f"{mechanism} verification was declined or failed")
        return True
    present = present_fn()
    if present is None:
        raise ConsentUnavailableError("no OS consent mechanism is available on this machine")
    if not present:
        raise ConsentDeniedError("the consent prompt was cancelled")
    return True


def _consent_windows(reason: str) -> bool:
    return _resolve_tier(
        lambda: _try_windows_hello(reason),
        lambda: _try_windows_credential_prompt(reason),
        mechanism="Windows Hello",
    )


def _consent_macos(reason: str) -> bool:
    return _resolve_tier(
        lambda: _try_macos_localauth(reason),
        lambda: _try_macos_presence_prompt(reason),
        mechanism="Touch ID / device-password",
    )


def _consent_linux(reason: str) -> bool:
    return _resolve_tier(
        lambda: _try_linux_pkexec(reason),
        lambda: _try_linux_presence_prompt(reason),
        mechanism="polkit",
    )


def _try_windows_hello(reason: str) -> bool | None:
    """Real Windows Hello verification. Returns True/False, or None if unavailable.

    Uses the WinRT UserConsentVerifier, which shows the native Windows Security
    panel (PIN / fingerprint / face). Requires Windows Hello to be configured AND
    the `winsdk` (or legacy `winrt`) bridge to be importable; returns None otherwise
    so the caller can fall back.
    """
    try:
        try:
            from winsdk.windows.security.credentials.ui import (  # type: ignore
                UserConsentVerificationResult,
                UserConsentVerifier,
                UserConsentVerifierAvailability,
            )
        except Exception:
            from winrt.windows.security.credentials.ui import (  # type: ignore
                UserConsentVerificationResult,
                UserConsentVerifier,
                UserConsentVerifierAvailability,
            )
    except Exception:
        return None

    try:
        availability = UserConsentVerifier.check_availability_async().get()
        if availability != UserConsentVerifierAvailability.AVAILABLE:
            # Hello is not set up on this machine; let the caller fall back.
            return None
        result = UserConsentVerifier.request_verification_async(reason).get()
        return result == UserConsentVerificationResult.VERIFIED
    except Exception:
        return None


def _try_windows_credential_prompt(reason: str) -> bool | None:
    """Native interactive credential dialog as a PRESENCE gate.

    Returns True if the user submitted the dialog, False if they cancelled, or None
    if the API is unavailable. This does NOT verify the entered credentials - it only
    proves a human actively confirmed at the keyboard (weaker than Hello). The entered
    buffer is discarded immediately and never inspected.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    try:
        credui = ctypes.WinDLL("credui.dll")

        class CREDUI_INFOW(ctypes.Structure):  # noqa: N801 - matches the Win32 struct name
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hwndParent", wintypes.HWND),
                ("pszMessageText", wintypes.LPCWSTR),
                ("pszCaptionText", wintypes.LPCWSTR),
                ("hbmBanner", wintypes.HBITMAP),
            ]

        info = CREDUI_INFOW()
        info.cbSize = ctypes.sizeof(CREDUI_INFOW)
        info.hwndParent = None
        info.pszMessageText = str(reason)
        info.pszCaptionText = _CONSENT_TITLE
        info.hbmBanner = None

        auth_package = wintypes.DWORD(0)
        out_cred_blob = ctypes.c_void_p(None)
        out_cred_size = wintypes.DWORD(0)
        save = wintypes.BOOL(False)

        # CREDUIWIN_GENERIC (0x1): generic credentials, no logon validation - we only
        # care whether the user pressed OK (0) vs Cancel (ERROR_CANCELLED = 1223).
        CREDUIWIN_GENERIC = 0x00000001
        result = credui.CredUIPromptForWindowsCredentialsW(
            ctypes.byref(info),
            0,
            ctypes.byref(auth_package),
            None,
            0,
            ctypes.byref(out_cred_blob),
            ctypes.byref(out_cred_size),
            ctypes.byref(save),
            CREDUIWIN_GENERIC,
        )

        if out_cred_blob:
            # Wipe and free the returned credential buffer; we never read it.
            with contextlib.suppress(Exception):
                ctypes.memset(out_cred_blob, 0, out_cred_size.value)
            ctypes.windll.ole32.CoTaskMemFree(out_cred_blob)

        if result == 0:
            return True
        if result == 1223:  # ERROR_CANCELLED
            return False
        # Any other return code: treat as unavailable so the caller fails closed.
        return None
    except Exception:
        return None


def _try_macos_localauth(reason: str) -> bool | None:
    """Real macOS device-owner auth (Touch ID / login password) via LocalAuthentication.

    The macOS analogue of Windows Hello. Returns True/False, or None when PyObjC's
    LocalAuthentication bridge is not importable (the common case), so the caller falls back
    to the presence prompt.
    """
    try:
        import objc  # noqa: F401  (PyObjC core; importing proves the bridge exists)
        from LocalAuthentication import (  # type: ignore
            LAContext,
            LAPolicyDeviceOwnerAuthentication,
        )
    except Exception:
        return None
    try:
        import threading

        ctx = LAContext.alloc().init()
        can, _err = ctx.canEvaluatePolicy_error_(LAPolicyDeviceOwnerAuthentication, None)
        if not can:
            return None
        outcome: dict[str, bool | None] = {"ok": None}
        done = threading.Event()

        def _reply(success, _error) -> None:  # Obj-C completion block reply
            outcome["ok"] = bool(success)
            done.set()

        ctx.evaluatePolicy_localizedReason_reply_(LAPolicyDeviceOwnerAuthentication, str(reason), _reply)
        if not done.wait(timeout=120):
            return None
        return outcome["ok"]
    except Exception:
        return None


def _try_macos_presence_prompt(reason: str) -> bool | None:
    """Presence gate via osascript: a modal dialog the user must actively approve.

    Parity with the Windows CredUI presence fallback - proves a live human confirmed, not identity.
    Returns True (approved), False (cancelled/timed out), or None if osascript is unavailable.
    """
    import shutil
    import subprocess

    if shutil.which("osascript") is None:
        return None
    safe = str(reason).replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'display dialog "{safe}" with title "{_CONSENT_TITLE}" '
        'buttons {"Cancel", "Approve"} default button "Approve" cancel button "Cancel" '
        "with icon caution giving up after 120"
    )
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=130)
    except Exception:
        return None
    # Explicit Approve => rc 0 + "button returned:Approve". Cancel => nonzero. Timeout => rc 0 + "gave up:true".
    return proc.returncode == 0 and "button returned:Approve" in (proc.stdout or "")


def _try_linux_pkexec(reason: str) -> bool | None:
    """Real password auth via polkit's pkexec (the Linux analogue of Windows Hello).

    `pkexec true` shows the polkit authentication dialog. Returns True (authenticated), False
    (dismissed/not authorized), or None when pkexec/polkit or its agent is unavailable so the
    caller falls back to a presence prompt.
    """
    import shutil
    import subprocess

    if shutil.which("pkexec") is None:
        return None
    logger.info("os consent: requesting polkit authorization for: %s", reason)
    try:
        proc = subprocess.run(["pkexec", "true"], capture_output=True, text=True, timeout=130)
    except Exception:
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 127:  # authorization could not be obtained (no polkit agent) -> unavailable
        return None
    return False  # 126 dismissed / not authorized, or any other failure -> deny


def _try_linux_presence_prompt(reason: str) -> bool | None:
    """Presence gate via zenity/kdialog. Returns True (approved), False (declined), or None when
    no GUI dialog tool is available (headless -> caller fails closed)."""
    import shutil
    import subprocess

    safe = str(reason)
    if shutil.which("zenity"):
        cmd = [
            "zenity",
            "--question",
            f"--title={_CONSENT_TITLE}",
            f"--text={safe}",
            "--ok-label=Approve",
            "--cancel-label=Cancel",
        ]
    elif shutil.which("kdialog"):
        cmd = ["kdialog", "--title", _CONSENT_TITLE, "--yesno", safe]
    else:
        return None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=130)
    except Exception:
        return None
    return proc.returncode == 0  # zenity/kdialog: 0 = Approve/Yes, nonzero = decline/close
