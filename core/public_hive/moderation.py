from __future__ import annotations

from typing import Any

from network.signer import get_local_peer_id


def submit_public_moderation_review(
    bridge: Any,
    *,
    object_type: str,
    object_id: str,
    decision: str,
    note: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {"ok": False, "status": "disabled"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}
    payload = {
        "object_type": str(object_type or "").strip(),
        "object_id": str(object_id or "").strip(),
        "reviewer_agent_id": get_local_peer_id(),
        "decision": str(decision or "").strip(),
        "note": " ".join(str(note or "").split()).strip()[:512] or None,
    }
    result = bridge._post_json(str(bridge.config.topic_target_url), "/v1/hive/moderation/reviews", payload)
    return {"ok": True, **result}
