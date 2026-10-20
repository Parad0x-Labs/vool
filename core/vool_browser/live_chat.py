"""Fail closed on an explicit browser request while its owner switch is off."""
from __future__ import annotations


def refuse_if_off(agent, text, *, session_id, source_context):
    from core.i18n import catalog_for
    from core.vool_browser import live, live_requests

    if not live_requests.requested(text) or live.available():
        return None
    cat = catalog_for(str((source_context or {}).get("ui_locale") or "en"))
    response = f'{cat.text("live.browser")}: {cat.text("live.stopped")}. [{cat.text("sidebar.settings")}](/settings#permissions)'
    result = agent._fast_path_result(session_id=session_id, user_input=text, response=response,
                                     confidence=1.0, source_context=source_context, reason="live_browser_off")
    result.update(deterministic=True, success=False, status="web_off", task_outcome="failed")
    return result
