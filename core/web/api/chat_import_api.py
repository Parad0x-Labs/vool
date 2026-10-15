"""HTTP doors for bringing chats from other AI apps into VOOL (Settings).

Every door is owner-only with the same guards as ``/api/x/setup``: the loopback peer only, no
cross-site request (``Sec-Fetch-Site: cross-site`` or a foreign ``Origin`` is refused), JSON
bodies only with a size cap and no unknown fields. The upload door takes raw bytes (an export zip)
with its own ceiling and answers with an opaque id, never a file path. The doors call
``core.chat_import.flow`` and return its typed answers; nothing here reads or logs a message.
"""
from __future__ import annotations

import json
from typing import Any

from core.request_trust import is_loopback_host

UPLOAD_PATH = "/api/chat/import/upload"
#: JSON body caps: the import body lists chosen chat refs (up to the reader's chat limit).
BODY_CAP = 64 * 1024
START_BODY_CAP = 1024 * 1024


def _header(headers: dict[str, Any] | None, name: str) -> str:
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return str(value or "")
    return ""


def _guard(headers: dict[str, Any] | None, client_host: str, *, json_body: bool) -> tuple[int, dict[str, Any]] | None:
    from core.web.api.runtime import host_header_allowed

    if not is_loopback_host(client_host) or _header(headers, "sec-fetch-site").lower() == "cross-site":
        return 403, {"ok": False, "error": "owner_local_required"}
    origin = _header(headers, "origin").strip()
    if origin and not host_header_allowed(origin.split("://", 1)[-1]):
        return 403, {"ok": False, "error": "cross_origin"}
    if json_body:
        content_type = _header(headers, "content-type").lower()
        if content_type and "json" not in content_type:
            return 415, {"ok": False, "error": "json_only"}
    return None


def _flow_error(exc: Exception) -> tuple[int, dict[str, Any]]:
    return int(getattr(exc, "status", 400)), {"ok": False, "error": str(getattr(exc, "code", "failed"))}


def handle_get(path: str, *, headers: dict[str, Any] | None, client_host: str) -> tuple[int, dict[str, Any]]:
    from core.chat_import import flow

    refused = _guard(headers, client_host, json_body=False)
    if refused is not None:
        return refused
    if path == "/api/chat/import/sources":
        return 200, {"ok": True, **flow.sources()}
    if path == "/api/chat/import/status":
        return 200, {"ok": True, **flow.status()}
    return 404, {"ok": False, "error": "not_found"}


def handle_post(path: str, body: dict[str, Any], *, headers: dict[str, Any] | None, client_host: str) -> tuple[int, dict[str, Any]]:
    from core.chat_import import flow

    refused = _guard(headers, client_host, json_body=True)
    if refused is not None:
        return refused
    try:
        size = len(json.dumps(body))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "unserializable_body"}
    if size > (START_BODY_CAP if path == "/api/chat/import/start" else BODY_CAP):
        return 413, {"ok": False, "error": "body_too_large"}
    allowed = {
        "/api/chat/import/preview": {"source", "upload_id"},
        "/api/chat/import/start": {"preview_id", "chats"},
        "/api/chat/import/discard": set(),
    }.get(path)
    if allowed is None:
        return 404, {"ok": False, "error": "not_found"}
    if set(body) - allowed:
        return 400, {"ok": False, "error": "unknown_fields"}
    try:
        if path == "/api/chat/import/preview":
            upload_id = body.get("upload_id")
            source = body.get("source")
            if isinstance(upload_id, str) and upload_id and source in (None, "", "file"):
                return 200, {"ok": True, **flow.preview_upload(upload_id)}
            if isinstance(source, str) and source and not upload_id:
                return 200, {"ok": True, **flow.preview_local(source)}
            return 400, {"ok": False, "error": "missing_source"}
        if path == "/api/chat/import/start":
            preview_id = body.get("preview_id")
            chats = body.get("chats")
            if not isinstance(preview_id, str) or not isinstance(chats, list):
                return 400, {"ok": False, "error": "missing_selection"}
            return 202, {"ok": True, **flow.start_import(preview_id, chats)}
        return 200, {"ok": True, **flow.discard()}
    except flow.FlowError as exc:
        return _flow_error(exc)


def handle_upload(raw_body: bytes, *, headers: dict[str, Any] | None, client_host: str) -> tuple[int, dict[str, Any]]:
    from core.chat_import import flow

    refused = _guard(headers, client_host, json_body=False)
    if refused is not None:
        return refused
    try:
        return 201, {"ok": True, **flow.stage_upload(bytes(raw_body or b""))}
    except flow.FlowError as exc:
        return _flow_error(exc)


def upload_ceiling() -> int:
    from core.chat_import.flow import MAX_UPLOAD_BYTES

    return MAX_UPLOAD_BYTES + 64 * 1024
