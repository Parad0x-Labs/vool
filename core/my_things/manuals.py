"""Explicit online manual preview through the existing authorized web tool.

No preview cache or auto-save. The owner attaches the preview only with a second action.
Binary downloads are refused: the web tool returns text, so pretending it delivered a PDF
would corrupt that file. A PDF the owner already has can be attached through the file door.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from core.my_things.store import ThingsError, ThingsStore


def preview(thing_id, url, *, confirmed=False):
    from core import policy_engine
    from core.authorized_tool_execution import execute_authorized_runtime_tool
    from core.remote_fetch_policy import effective_web_available

    if not policy_engine.allow_web_fallback() or not effective_web_available():
        raise ThingsError("online_off", "Online access is off. You can attach a manual you already have.", 409)
    if confirmed is not True:
        raise ThingsError("confirmation_required", "Review the manual address, then confirm before opening it online.", 409)
    if not isinstance(url, str) or len(url) > 2000:
        raise ThingsError("bad_url", "Enter the manual's web address.")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ThingsError("bad_url", "Enter a public web address without sign-in details.")
    ThingsStore().get(thing_id)
    # This does not grant network authority. The existing policy and permission
    # boundary decide, with their ordinary approval/refusal semantics intact.
    session = "my-things-manual:" + thing_id
    result = execute_authorized_runtime_tool("web.fetch", {"url": url, "timeout_s": 12}, task_id=session,
        source_context={"surface": "web_ui", "_owner_local": True, "session_id": session, "runtime_session_id": session})
    if result is not None and result.status == "pending_approval":
        return {"requires_approval": True, "approval_request": result.details.get("approval_request", {}), "url": url, "saved": False, "session_id": session}
    if result is None or not result.ok:
        raise ThingsError("manual_refused", "The web tool did not open this manual. Check your online permissions, or attach a file you already have.", 409)
    details = result.details
    content_type = str(details.get("content_type", "")).split(";", 1)[0].lower()
    if details.get("truncated") or content_type not in {"text/plain", "text/html", "application/xhtml+xml"}:
        raise ThingsError("manual_format", "This manual could not be read completely as text. Download it yourself and attach the file.", 409)
    text = details.get("text", "")
    if not text or len(text.encode("utf-8")) > 256000:
        raise ThingsError("manual_format", "This manual is empty or too large to preview.", 409)
    return {"text": text, "url": details.get("final_url", url), "requires_confirmation": True, "saved": False}
