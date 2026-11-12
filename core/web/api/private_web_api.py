"""Owner-only doors for Private web (Settings): read its state, choose a route, add a VPN file."""
from __future__ import annotations

ACTIONS = ("settings", "vpn_file", "forget_vpn_file")
_MAX_VPN_FILE_BYTES = 16 * 1024


def get(path, query=None):
    from core import private_web_service
    from core.web.api.service import json_response

    if path != "/api/private-web":
        return json_response(404, {"ok": False, "error": "unsupported"})
    return json_response(200, private_web_service.status(), headers={"Cache-Control": "no-store"})


def post(path, body, *, headers=None, client_host=""):
    from core.command_registry.legacy import forward
    from core.web.api.service import json_response

    if not isinstance(body, dict):
        return json_response(400, {"ok": False, "error": "invalid_input"})
    action = path.removeprefix("/api/private-web/")
    if action not in ACTIONS:
        return json_response(404, {"ok": False, "error": "unsupported"})
    status, result = forward("private_web." + action, {"body": body}, headers=headers, client_host=client_host)
    return json_response(status, result, headers={"Cache-Control": "no-store"})


def perform(path, body):
    from core import private_web_service
    from core.web.api.service import json_response
    from core.wireguard_runtime import WireGuardFileProblem

    try:
        if path == "/api/private-web/settings":
            return json_response(200, private_web_service.set_route(str(body.get("route") or "")))
        if path == "/api/private-web/vpn_file":
            text = body.get("text")
            if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > _MAX_VPN_FILE_BYTES:
                return json_response(400, {"ok": False, "error": "This does not look like a WireGuard file."})
            return json_response(200, private_web_service.save_vpn_file(text))
        if path == "/api/private-web/forget_vpn_file":
            return json_response(200, private_web_service.forget_vpn_file())
    except WireGuardFileProblem as exc:
        return json_response(400, {"ok": False, "error": str(exc)})
    except ValueError:
        return json_response(400, {"ok": False, "error": "invalid_input"})
    return json_response(404, {"ok": False, "error": "unsupported"})
