"""The download door for files the assistant built in a chat (`core.deliverables`).

``GET /api/chat/deliverable?id=dlv_...&session=<chat>`` -- owner-local only, the same loopback
trust the chat export door uses. The id alone is not enough: the file is served only to the chat
that created it, verified against its stored hash, and always as an attachment (never rendered
inline by the browser), with the filename the user will see on disk.
"""

from __future__ import annotations

from urllib.parse import quote

from core.web.api.service import (
    ApiResponse,
    RuntimeServices,
    apply_runtime_headers,
    is_loopback_host,
    json_response,
)


def _ascii_fallback(name: str) -> str:
    cleaned = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\;' else "_" for ch in name)
    return cleaned or "vool-file"


def handle_deliverable_get(*, query: dict[str, list[str]], runtime: RuntimeServices, client_host: str = "") -> ApiResponse:
    if not is_loopback_host(client_host):
        return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
    deliverable_id = str((query.get("id") or [""])[0] or "").strip()
    session_id = str((query.get("session") or [""])[0] or "").strip()
    if not deliverable_id or not session_id:
        return apply_runtime_headers(
            json_response(400, {"ok": False, "error": "id_and_session_required", "message": "Which file, from which chat?"}),
            runtime,
        )
    from core import deliverables

    found = deliverables.read_deliverable(deliverable_id=deliverable_id, session_id=session_id)
    if found is None:
        return apply_runtime_headers(
            json_response(
                404,
                {
                    "ok": False,
                    "error": "not_found",
                    "message": f"That file is not available. Files are kept for {deliverables.RETENTION_SECONDS // 86400} days; ask for it again to rebuild it.",
                },
            ),
            runtime,
        )
    record, data = found
    filename = str(record.get("filename") or "vool-file")
    return apply_runtime_headers(
        ApiResponse(
            status=200,
            content_type=str(record.get("media_type") or "application/octet-stream"),
            body=data,
            headers={
                "Content-Disposition": f"attachment; filename=\"{_ascii_fallback(filename)}\"; filename*=UTF-8''{quote(filename, safe='')}",
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
            },
        ),
        runtime,
    )
