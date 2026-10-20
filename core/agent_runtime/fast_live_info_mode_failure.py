from __future__ import annotations

import os


def live_info_failure_text(*, query: str, mode: str) -> str:
    # Every wording below reports a web lookup that ran. With web lookups off no lookup can
    # run (the live-info preflight refuses before any search), so a caller that reaches this
    # renderer anyway -- the degraded chat fallback after the refusal was set aside -- must
    # say the web is off and nothing was checked, not claim a check that never happened.
    from core import policy_engine

    if not policy_engine.allow_web_fallback():
        from core.i18n.turn_locale import turn_text

        if _web_forced_off_at_start():
            # Started with web lookups forced off (VOOL_DISABLE_WEB / VOOL_NO_WEB), which the
            # Settings switch cannot override, so the notice does not point there.
            return turn_text(
                "liveinfo.web_off.not_checked_started_off",
                "Web lookups are off, so I did not search the web and can't verify a current answer.",
            )
        return turn_text(
            "liveinfo.web_off.not_checked",
            "Web lookups are off, so I did not search the web and can't verify a current answer. "
            "You can turn them on in Settings → Privacy & Permissions → Web lookups.",
        )
    if mode == "weather":
        return f'I tried the live web lane for "{query}", but no current weather results came back.'
    if mode == "news":
        return f'I tried the live web lane for "{query}", but no current news results came back.'
    return f'I checked the live web lane for "{query}", but I could not ground a current answer confidently.'


def _web_forced_off_at_start() -> bool:
    # The same variables and truthy values policy_engine's environment overrides read.
    return any(
        str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}
        for name in ("VOOL_DISABLE_WEB", "VOOL_NO_WEB")
    )
