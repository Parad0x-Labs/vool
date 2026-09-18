"""Bounded access to the OS keyring — the ONE authority for the rule that a keyring
call may never block the runtime indefinitely.

macOS Keychain authorization: a call from a binary the Keychain has never seen (a
fresh sandbox install, an unsigned embedded interpreter) does not raise — it BLOCKS
on the GUI authorization dialog while the caller waits forever. First boot of the
self-contained app hung twice on exactly this (node signing key write, then the
credential read during default-provider registration); the 90s native-runtime
readiness gate expired and no window ever opened.

Every backend access therefore runs in a bounded daemon thread. Native bootstrap
uses its host's shared authorization deadline while the operator answers the OS
dialog; ordinary and unattended calls retain the short IO bound. A pending prompt
surfaces as ``TimeoutError`` so the caller's own fallback semantics decide (auto
storage modes degrade to their file fallback; credential reads report absent).
The worker thread is a daemon: if the prompt is eventually answered nothing is
lost, and the next boot re-tries the keyring path.

Callers must treat ``TimeoutError`` exactly like a backend failure — never retry
in a loop, because the prompt can outlive any retry budget.
"""

from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

#: Bound for one keyring read/write. Local Keychain operations that are not
#: waiting on a human complete in well under a second; anything slower is a
#: dialog waiting for a click.
DEFAULT_TIMEOUT_S = 10.0

# Only the native bootstrap caller enters this scope. A single deadline covers
# successive authorization dialogs; each key access must not restart the budget.
_AUTHORIZATION_DEADLINE: ContextVar[float | None] = ContextVar("keyring_authorization_deadline", default=None)


@contextmanager
def native_bootstrap_authorization(deadline: str | None):
    """Use the native owner's remaining startup budget, then restore normal IO bounds."""
    value = float(deadline) if deadline else None
    if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValueError("invalid native authorization deadline")
    token = _AUTHORIZATION_DEADLINE.set(value)
    try:
        yield
    finally:
        _AUTHORIZATION_DEADLINE.reset(token)

#: Process-wide circuit breaker (2026-09-02 operator addendum): a timed-out
#: keyring call means a macOS authorization dialog is pending that this process
#: CANNOT cancel. Every further attempt would stack another prompt, so the
#: breaker records the block once and every later keyring access in this
#: process must short-circuit to the non-interactive fallback instead.
_KEYCHAIN_BLOCKED: bool = False


def keychain_blocked() -> bool:
    """True once a keyring call in this process timed out on a pending prompt."""
    return _KEYCHAIN_BLOCKED


def note_keychain_blocked() -> None:
    global _KEYCHAIN_BLOCKED
    _KEYCHAIN_BLOCKED = True


def bounded_keyring_call(fn, *, what: str, timeout_s: float | None = None):
    """Run ``fn()`` (a keyring backend call) with a hard wall-clock bound.

    Returns fn()'s return value, re-raises fn()'s exception, or raises
    ``TimeoutError`` when the call is still blocked after ``timeout_s`` seconds.
    A timeout also arms the process-wide circuit breaker: callers must consult
    :func:`keychain_blocked` BEFORE initiating any further keyring access.
    """
    timeout = DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s
    deadline = _AUTHORIZATION_DEADLINE.get()
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native Keychain authorization window expired; no new call started")
        timeout = remaining if timeout_s is None else min(timeout, remaining)
    outcome: dict[str, object] = {}

    def _run() -> None:
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # re-raised in the caller below
            outcome["error"] = exc

    worker = threading.Thread(target=_run, name=f"vool-keyring-{what}", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        note_keychain_blocked()
        raise TimeoutError(
            f"keyring {what} did not complete within {timeout}s "
            "(a Keychain authorization prompt is likely pending)"
        )
    error = outcome.get("error")
    if error is not None:
        if _is_authorization_denial(error):
            note_keychain_blocked()
        raise error  # type: ignore[misc]
    return outcome.get("value")


#: macOS signals a denied Keychain authorization immediately -- errSecAuthFailed
#: (-25293) or a locked keychain -- rather than by hanging, so the timeout branch
#: above never saw it. Matched on the OS's own codes and on the exception type
#: name, never on a broad "any exception".
_DENIAL_MARKERS = (
    "errsecauthfailed",
    "-25293",
    "errsecinteractionnotallowed",
    "-25308",
    "user canceled",
    "user cancelled",
    "authorization denied",
    "keyringlocked",
)


def _is_authorization_denial(error: BaseException) -> bool:
    """Whether this failure means "the operator (or the OS) said no".

    NARROW on purpose. Arming the breaker on ANY keyring exception was tried and
    reverted: ``PasswordDeleteError`` is raised for a credential that is simply
    ABSENT -- an ordinary, healthy outcome -- and treating it as a denial armed the
    breaker on a routine delete and disabled keychain access for the rest of the
    process. Ten tests said so immediately.

    A denial, by contrast, means a prompt was shown and refused, and repeating the
    sweep would show it again. That is the repeated-dialog storm the unattended law
    forbids: ``_reconcile_all`` walks every credential slot catching per-name, and
    ``list_credentials`` re-runs that sweep on every call -- measured at 11 probes
    per call, 33 across three, with the breaker still unarmed.
    """
    name = type(error).__name__.lower()
    if "locked" in name or "denied" in name:
        return True
    text = f"{error}".lower()
    return any(marker in text for marker in _DENIAL_MARKERS)
