"""Thin owner-only adapters for the supervised browser."""
from __future__ import annotations

from core.vool_browser import live


def get(path, query, *, locale="en", appearance=None):
    from core.web.api.service import ApiResponse, html_response, json_response

    if path not in {"/browser", "/api/browser/settings", "/api/browser/state", "/api/browser/download", "/api/browser/window"}:
        return json_response(404, {"ok": False, "error": "unsupported"})
    chat = (query.get("chat") or [""])[0]
    if path == "/browser":
        from core.vool_browser import guard
        from core.vool_browser.live_page import render

        # The warning page in the browser window speaks the language the pane was last seen in.
        guard.remember_language(locale)

        return html_response(200, render(chat, locale, appearance), headers={"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN", "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'"})
    if path == "/api/browser/settings":
        return json_response(200, live.settings(), headers={"Cache-Control": "no-store"})
    if path == "/api/browser/window":
        from core.vool_browser import desk

        return json_response(200, desk.desk().status(), headers={"Cache-Control": "no-store"})
    try:
        item = live.session(chat)
        active_chat = live.chat_active(chat) if item is None else True
    except live.BrowserFault as exc:
        return json_response(409, {"ok": False, "error": str(exc)})
    if path == "/api/browser/download" and item:
        download = item.download()
        if download:
            return ApiResponse(200, "application/octet-stream", body=download[1], headers={"Content-Disposition": 'attachment; filename="download.bin"', "Cache-Control": "no-store"})
    if path == "/api/browser/state" and item:
        item.owner_heartbeat()
    return json_response(200, item.snapshot() if item else {"ok": True, "state": "stopped" if not active_chat else "ready" if live.available() else "web_off", "closed": True}, headers={"Cache-Control": "no-store"})


def before_chat_end(chat, *, locale="en"):
    from core.i18n import catalog_for
    from core.web.api.service import json_response

    try:
        live.stop_chat(chat)
    except live.BrowserFault:
        return json_response(409, {"error": catalog_for(locale).text("live.failed"), "code": "browser_cleanup_failed"})
    return None


def post(path, body, *, headers=None, client_host=""):
    from core.command_registry.legacy import forward
    from core.web.api.service import json_response

    if not isinstance(body, dict):
        return json_response(400, {"ok": False, "error": "invalid_input"})
    action = path.removeprefix("/api/browser/")
    if action not in {"settings", "propose", "decide", "takeover", "stop", "window"}:
        return json_response(404, {"ok": False, "error": "unsupported"})
    status, result = forward("browser." + action, {"body": body}, headers=headers, client_host=client_host)
    return json_response(status, result, headers={"Cache-Control": "no-store"})


def perform(path, body):
    from core.web.api.service import json_response

    try:
        if path == "/api/browser/settings":
            if "agent_in_window" in body and "enabled" not in body:
                return json_response(200, live.set_agent_in_window(body.get("agent_in_window")))
            return json_response(200, live.set_enabled(body.get("enabled")))
        if path == "/api/browser/window":
            return json_response(200, _window(body), headers={"Cache-Control": "no-store"})
        chat = str(body.get("chat") or "")
        item = live.session(chat, create=path == "/api/browser/propose")
        if not item:
            raise live.BrowserFault("chat_required")
        if path == "/api/browser/stop":
            item.stop()
            result = item.snapshot()
        elif path == "/api/browser/takeover":
            if type(body.get("value")) is not bool:
                raise live.BrowserFault("invalid_input")
            result = item.take_over(body["value"])
        elif path == "/api/browser/decide":
            if type(body.get("approve")) is not bool:
                raise live.BrowserFault("invalid_input")
            result = item.decide(str(body.get("id") or ""), body["approve"])
        elif path == "/api/browser/propose":
            result = item.propose(str(body.get("op") or ""), dict(body.get("args") or {}), actor="owner")
            if body.get("now") is True:
                result = item.run_owner_step(result["proposal"]["id"]) or result
        else:
            raise live.BrowserFault("unsupported")
        return json_response(200, result, headers={"Cache-Control": "no-store"})
    except (live.BrowserFault, ValueError, TypeError) as exc:
        return json_response(409, {"ok": False, "error": str(exc) if isinstance(exc, live.BrowserFault) else "invalid_input"})


def _window(body):
    """The everyday browser window: open an address, open a private window, or close it all."""
    from core.vool_browser import desk

    action = body.get("action")
    if action not in ("open", "private", "stop", "show") or not isinstance(body.get("url", ""), str):
        raise live.BrowserFault("invalid_input")
    if action == "show":
        # The tab VOOL works in for this chat, brought to the front for the person.
        item = live.session(str(body.get("chat") or ""))
        return {"ok": True, "shown": bool(item and item.in_window and item.show())}
    try:
        if action == "stop":
            return desk.desk().stop()
        return desk.desk().open(body.get("url", ""), private=action == "private")
    except desk.DeskFault as exc:
        raise live.BrowserFault(str(exc)) from None
