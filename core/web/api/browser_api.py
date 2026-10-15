"""Thin owner-only adapters for the supervised browser."""
from __future__ import annotations

from core.vool_browser import live


def get(path, query, *, locale="en"):
    from core.web.api.service import ApiResponse, html_response, json_response

    if path not in {"/browser", "/api/browser/settings", "/api/browser/state", "/api/browser/download"}:
        return json_response(404, {"ok": False, "error": "unsupported"})
    chat = (query.get("chat") or [""])[0]
    if path == "/browser":
        from core.vool_browser.live_page import render

        return html_response(200, render(chat, locale), headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"})
    if path == "/api/browser/settings":
        return json_response(200, live.settings(), headers={"Cache-Control": "no-store"})
    item = live.session(chat)
    if path == "/api/browser/download" and item:
        download = item.download()
        if download:
            return ApiResponse(200, "application/octet-stream", body=download[1], headers={"Content-Disposition": 'attachment; filename="download.bin"', "Cache-Control": "no-store"})
    return json_response(200, item.snapshot() if item else {"ok": True, "state": "ready" if live.available() else "web_off", "closed": True}, headers={"Cache-Control": "no-store"})


def post(path, body, *, headers=None, client_host=""):
    from core.command_registry.legacy import forward
    from core.web.api.service import json_response

    if not isinstance(body, dict):
        return json_response(400, {"ok": False, "error": "invalid_input"})
    action = path.removeprefix("/api/browser/")
    if action not in {"settings", "propose", "decide", "takeover", "stop"}:
        return json_response(404, {"ok": False, "error": "unsupported"})
    status, result = forward("browser." + action, {"body": body}, headers=headers, client_host=client_host)
    return json_response(status, result, headers={"Cache-Control": "no-store"})


def perform(path, body):
    from core.web.api.service import json_response

    try:
        if path == "/api/browser/settings":
            return json_response(200, live.set_enabled(body.get("enabled")))
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
        else:
            raise live.BrowserFault("unsupported")
        return json_response(200, result, headers={"Cache-Control": "no-store"})
    except (live.BrowserFault, ValueError, TypeError) as exc:
        return json_response(409, {"ok": False, "error": str(exc) if isinstance(exc, live.BrowserFault) else "invalid_input"})
