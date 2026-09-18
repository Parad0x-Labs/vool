"""
core/self_update_offer.py
=========================
In-chat surface for the self-update feature: proactively offer a detected update and
act on the user's yes/no — without ever hijacking a real question.

It is a pre-agent short-circuit (works for streaming and non-streaming turns). It only
returns a response when:
  * the user explicitly asks to apply ("update now", "install the update"), OR
  * a bare yes/no answers an offer shown earlier this session, OR
  * an update is available and the user's message is a greeting/idle line — the one
    case where surfacing an unsolicited offer doesn't interrupt real work.

Applying never runs unverified code or touches user data: it launches the detached
updater, which checks the package checksum, backs up, swaps, restarts, and rolls back
on failure. The wallet, spend policy, and DB live outside the code dir.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# Explicit "apply the update" intents (anchored / phrase-bound to avoid false positives
# like "update my readme").
_APPLY_RE = re.compile(
    r"^\s*(?:yes[,\s]+)?(?:please\s+)?(?:go\s+ahead\s+and\s+)?(?:update|upgrade)"
    r"(?:\s+(?:now|vool|it|please|yourself))*\s*[.!]*\s*$",
    re.IGNORECASE,
)
_APPLY_PHRASE_RE = re.compile(r"^\s*(?:please\s+)?(?:install|do|apply|run)\s+the\s+update\b", re.IGNORECASE)

_STRICT_YES = re.compile(
    r"^\s*(?:yes|yep|yeah|yup|ok(?:ay)?|sure|do\s+it|go(?:\s+ahead)?|proceed)\s*[.!]*\s*$",
    re.IGNORECASE,
)
_STRICT_NO = re.compile(
    r"^\s*(?:no|nope|nah|not\s+now|later|skip|dismiss|don'?t)\s*[.!]*\s*$",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(
    r"^\s*(?:hi|hey+|hello|hiya|yo|sup|gm|ge|good\s+(?:morning|evening|afternoon)|howdy|"
    r"what'?s\s+up|wass?up|thanks|thank\s+you|ty)\b",
    re.IGNORECASE,
)


def _is_apply_intent(text: str) -> bool:
    clean = str(text or "")
    return bool(_APPLY_RE.match(clean) or _APPLY_PHRASE_RE.search(clean))


def _response(text: str, *, intent: str, success: bool = True) -> dict[str, Any]:
    return {
        "response": text,
        "confidence": 1.0,
        "source": "self_update_offer",
        "deterministic": True,
        "intent": intent,
        "success": success,
    }


def _offer_text(installed: str, target: str, changelog: list[str]) -> str:
    lines = "\n".join(f"  - {c}" for c in (changelog or [])[:8]) or "  - (no changelog provided)"
    return (
        f"🔔 I found a VOOL update: **{target}** (you're on {installed}). What's new:\n"
        f"{lines}\n\n"
        f"Reply **update now** to install — I back up the current version, swap, restart, and "
        f"automatically roll back if the new one doesn't come up. Your wallet, spend policy, and "
        f"data are never touched. Reply **no** to skip this version."
    )


def _apply_text(target: str, launched: bool) -> str:
    if launched:
        return (
            f"Starting the update to **{target}** in the background — VOOL will stop, swap, and "
            f"restart. If the new version doesn't come up healthy, it rolls back automatically. "
            f"Give it a minute."
        )
    return (
        f"I couldn't start the updater for {target} from here. Run `vool update --apply` in a "
        f"terminal — same verified, auto-rollback flow."
    )


def maybe_handle_update_offer(
    user_text: str,
    session_id: str,
    *,
    availability_loader: Callable[[], Any] | None = None,
    check_state_loader: Callable[[], Any] | None = None,
    check_state_saver: Callable[[Any], None] | None = None,
    launcher: Callable[[Any], bool] | None = None,
    mark_offered_fn: Callable[[str, str], None] | None = None,
    load_offered_fn: Callable[[str], Any] | None = None,
    clear_offered_fn: Callable[[str], None] | None = None,
    installed_version_loader: Callable[[], str] | None = None,
) -> dict[str, Any] | None:
    text = str(user_text or "")
    sid = str(session_id or "").strip()

    if availability_loader is None:
        from core.self_update_state import load_available as availability_loader
    if check_state_loader is None:
        from core.self_update_state import load_check_state as check_state_loader
    if check_state_saver is None:
        from core.self_update_state import save_check_state as check_state_saver
    if launcher is None:
        launcher = _default_launcher
    if mark_offered_fn is None:
        from storage.self_update_offer_store import mark_offered as mark_offered_fn
    if load_offered_fn is None:
        from storage.self_update_offer_store import load_offered as load_offered_fn
    if clear_offered_fn is None:
        from storage.self_update_offer_store import clear_offered as clear_offered_fn
    if installed_version_loader is None:
        from core.app_version import installed_version as installed_version_loader

    av = availability_loader()
    dismissed = ""
    try:
        dismissed = str(getattr(check_state_loader(), "dismissed_version", "") or "")
    except Exception:
        dismissed = ""
    target = str(getattr(av, "target_version", "") or "") if av else ""
    # Require the cached target to be strictly newer than the LIVE installed version, so a
    # just-applied update (whose cache hasn't been refreshed yet) is never re-offered/re-applied.
    #
    # The installed version arrives through `installed_version_loader`, which defaults to the real
    # `core.app_version.installed_version` -- so production behaviour is unchanged and still reads
    # the live version, never the cached `av.installed_version` (that one is display text and a
    # stale cache is exactly what this gate exists to catch). It is injectable for the same reason
    # every other collaborator here is: this gate's whole contract is a comparison against the
    # installed version, and a test that cannot set both sides of that comparison can only assert
    # it by accident of whatever the app version happens to be that week.
    strictly_newer = False
    if target:
        try:
            from core.self_update_check import is_newer_version

            strictly_newer = is_newer_version(target, str(installed_version_loader() or ""))
        except Exception:
            strictly_newer = True  # fail open to the other gates below
    installable = bool(
        av
        and getattr(av, "available", False)
        and getattr(av, "asset_url", "")
        and getattr(av, "sha256_url", "")
        and target != dismissed
        and strictly_newer
    )

    # (1) Explicit apply intent — act whenever an update is actually installable.
    if installable and _is_apply_intent(text):
        launched = bool(launcher(av))
        if sid:
            clear_offered_fn(sid)
        return _response(_apply_text(target, launched), intent="self_update_apply", success=launched)

    # (2) A bare yes/no answering an offer shown earlier this session.
    pending = load_offered_fn(sid) if sid else None
    if pending is not None:
        if installable and _STRICT_YES.match(text):
            launched = bool(launcher(av))
            clear_offered_fn(sid)
            return _response(_apply_text(target, launched), intent="self_update_apply", success=launched)
        if _STRICT_NO.match(text):
            clear_offered_fn(sid)
            _dismiss(check_state_loader, check_state_saver, target)
            return _response(f"Okay — skipping {target or 'that update'}. I won't nag again until a newer one lands.", intent="self_update_dismissed")

    # (3) Proactive, non-intrusive offer: only on a greeting/idle line.
    if installable and sid and _GREETING_RE.match(text):
        already = load_offered_fn(sid)
        already_ver = str(already.get("target_version")) if isinstance(already, dict) else ""
        if already is None or already_ver != target:
            mark_offered_fn(sid, target)
            installed = str(getattr(av, "installed_version", "") or "")
            return _response(
                _offer_text(installed, target, list(getattr(av, "changelog", []) or [])),
                intent="self_update_offer",
            )
    return None


def _dismiss(check_state_loader: Callable[[], Any], check_state_saver: Callable[[Any], None], target: str) -> None:
    try:
        state = check_state_loader()
        state.dismissed_version = str(target or "")
        check_state_saver(state)
    except Exception:
        pass


def _default_launcher(av: Any) -> bool:
    """Launch through the ONE updater authority (core/updater).

    The legacy detached sha-sidecar updater (installer/self_update.spawn_detached_update)
    is retired from production: the signed-manifest flow re-verifies everything at press
    time, swaps atomically, restarts via the external helper, and auto-rolls-back."""
    from core.updater.runtime import boot_update_subsystem, get_update_subsystem

    boot_update_subsystem()
    subsystem = get_update_subsystem()
    if subsystem is None:
        return False
    return subsystem.press_full_from_chat(str(getattr(av, "target_version", "") or ""))


__all__ = ["maybe_handle_update_offer"]
