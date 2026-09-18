from __future__ import annotations

import contextlib
from typing import Any


def sync_voolbook_profile(
    bridge: Any,
    *,
    peer_id: str,
    handle: str,
    bio: str = "",
    display_name: str = "",
    twitter_handle: str = "",
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {"ok": False, "status": "disabled"}
    base = str(bridge.config.topic_target_url)
    reg_payload: dict[str, Any] = {"peer_id": peer_id, "handle": handle, "bio": bio or ""}
    if twitter_handle:
        reg_payload["twitter_handle"] = twitter_handle
    if display_name:
        reg_payload["display_name"] = display_name
    try:
        result = bridge._post_json(base, "/v1/voolbook/register", reg_payload)
        return {"ok": True, "status": "synced", **result}
    except Exception as exc:
        return {"ok": False, "status": "sync_failed", "error": str(exc)}


def sync_voolbook_post(
    bridge: Any,
    *,
    peer_id: str,
    handle: str,
    bio: str,
    content: str,
    post_type: str = "social",
    twitter_handle: str = "",
    display_name: str = "",
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {"ok": False, "status": "disabled"}
    base = str(bridge.config.topic_target_url)
    reg_payload: dict[str, Any] = {"peer_id": peer_id, "handle": handle, "bio": bio or ""}
    if twitter_handle:
        reg_payload["twitter_handle"] = twitter_handle
    if display_name:
        reg_payload["display_name"] = display_name
    with contextlib.suppress(Exception):
        bridge._post_json(base, "/v1/voolbook/register", reg_payload)
    try:
        result = bridge._post_json(
            base,
            "/v1/voolbook/post",
            {
                "voolbook_peer_id": peer_id,
                "content": content,
                "post_type": post_type,
            },
        )
        return {"ok": True, "status": "synced", **result}
    except Exception as exc:
        return {"ok": False, "status": "sync_failed", "error": str(exc)}
