from __future__ import annotations

from typing import Any

from core.brain_hive_models import (
    HivePostCreateRequest,
    HiveTopicClaimRequest,
    HiveTopicCreateRequest,
    HiveTopicDeleteRequest,
    HiveTopicStatusUpdateRequest,
    HiveTopicUpdateRequest,
)
from core.privacy_guard import text_privacy_risks
from core.public_hive.reads import list_public_topic_claims
from network.signer import get_local_peer_id


def update_public_topic_status(
    bridge: Any,
    *,
    topic_id: str,
    status: str,
    note: str | None = None,
    claim_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {"ok": False, "status": "disabled"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}
    try:
        result = update_topic_status(
            bridge,
            topic_id=topic_id,
            status=status,
            note=note,
            claim_id=claim_id,
            idempotency_key=idempotency_key,
        )
    except (RuntimeError, ValueError) as exc:
        error_text = str(exc or "").strip()
        if "Unknown POST path" in error_text and "/v1/hive/topic-status" in error_text:
            return {"ok": False, "status": "route_unavailable", "error": error_text}
        if "Only the creating agent can update this Hive topic." in error_text:
            return {"ok": False, "status": "not_owner", "error": error_text}
        if "Only the claiming agent can finalize the claim via topic status update." in error_text:
            return {"ok": False, "status": "not_owner", "error": error_text}
        if "already claimed" in error_text.lower():
            return {"ok": False, "status": "already_claimed", "error": error_text}
        if "Unknown topic claim:" in error_text or "Topic claim does not belong" in error_text:
            return {"ok": False, "status": "invalid_claim", "error": error_text}
        if "Only active claims can drive Hive topic status updates." in error_text:
            return {"ok": False, "status": "invalid_claim", "error": error_text}
        if "Claim-backed Hive topic status updates only support" in error_text:
            return {"ok": False, "status": "invalid_status", "error": error_text}
        raise
    return {"ok": True, **result}


def update_public_topic(
    bridge: Any,
    *,
    topic_id: str,
    title: str | None = None,
    summary: str | None = None,
    topic_tags: list[str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {"ok": False, "status": "disabled"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}
    combined = "\n".join(part for part in (str(title or "").strip(), str(summary or "").strip()) if part)
    if combined and text_privacy_risks(combined):
        return {"ok": False, "status": "privacy_blocked_topic"}
    request = HiveTopicUpdateRequest(
        topic_id=str(topic_id or "").strip(),
        updated_by_agent_id=get_local_peer_id(),
        title=" ".join(str(title or "").split()).strip()[:180] or None,
        summary=" ".join(str(summary or "").split()).strip()[:4000] or None,
        topic_tags=[str(item).strip()[:64] for item in list(topic_tags or []) if str(item).strip()][:16] or None,
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    try:
        result = bridge._post_json(
            str(bridge.config.topic_target_url),
            "/v1/hive/topic-update",
            request.model_dump(mode="json"),
        )
    except (RuntimeError, ValueError) as exc:
        error_text = str(exc or "").strip()
        if "Unknown POST path" in error_text and "/v1/hive/topic-update" in error_text:
            return {"ok": False, "status": "route_unavailable", "error": error_text}
        if "Only the creating agent can edit this Hive topic." in error_text:
            return {"ok": False, "status": "not_owner", "error": error_text}
        raise
    return {
        "ok": bool(result.get("topic_id")),
        "status": "updated" if result.get("topic_id") else "topic_update_failed",
        "topic_id": str(result.get("topic_id") or ""),
        "topic_result": result,
    }


def delete_public_topic(
    bridge: Any,
    *,
    topic_id: str,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {"ok": False, "status": "disabled"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}
    request = HiveTopicDeleteRequest(
        topic_id=str(topic_id or "").strip(),
        deleted_by_agent_id=get_local_peer_id(),
        note=" ".join(str(note or "").split()).strip()[:512] or None,
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    try:
        result = bridge._post_json(
            str(bridge.config.topic_target_url),
            "/v1/hive/topic-delete",
            request.model_dump(mode="json"),
        )
    except (RuntimeError, ValueError) as exc:
        error_text = str(exc or "").strip()
        if "Unknown POST path" in error_text and "/v1/hive/topic-delete" in error_text:
            return {"ok": False, "status": "route_unavailable", "error": error_text}
        if "Only the creating agent can delete this Hive topic." in error_text:
            return {"ok": False, "status": "not_owner", "error": error_text}
        if "already claimed" in error_text.lower():
            return {"ok": False, "status": "already_claimed", "error": error_text}
        if "Only open, unclaimed Hive topics can be deleted." in error_text:
            return {"ok": False, "status": "not_deletable", "error": error_text}
        raise
    return {
        "ok": bool(result.get("topic_id")),
        "status": "deleted" if result.get("topic_id") else "topic_delete_failed",
        "topic_id": str(result.get("topic_id") or ""),
        "topic_result": result,
    }


def create_public_topic(
    bridge: Any,
    *,
    title: str,
    summary: str,
    topic_tags: list[str] | None = None,
    status: str = "open",
    visibility: str = "read_public",
    evidence_mode: str = "candidate_only",
    linked_task_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled():
        return {"ok": False, "status": "disabled"}
    if not bridge.config.topic_target_url:
        return {"ok": False, "status": "missing_target"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}

    clean_title = " ".join(str(title or "").split()).strip()[:180]
    clean_summary = " ".join(str(summary or "").split()).strip()[:4000]
    if not clean_title or not clean_summary:
        return {"ok": False, "status": "empty_topic"}
    if text_privacy_risks(f"{clean_title}\n{clean_summary}"):
        return {"ok": False, "status": "privacy_blocked_topic"}

    display_name: str | None = None
    try:
        from core.voolbook_identity import get_profile

        profile = get_profile(get_local_peer_id())
        if profile and profile.handle:
            display_name = profile.handle.strip()[:64] or None
    except Exception:
        pass
    if not display_name:
        try:
            from core.agent_name_registry import get_agent_name

            display_name = (get_agent_name(get_local_peer_id()) or "")[:64] or None
        except Exception:
            pass

    request = HiveTopicCreateRequest(
        created_by_agent_id=get_local_peer_id(),
        creator_display_name=display_name,
        title=clean_title,
        summary=clean_summary,
        topic_tags=[str(item).strip()[:64] for item in list(topic_tags or []) if str(item).strip()][:16],
        status=str(status or "open").strip() or "open",
        visibility=str(visibility or "read_public").strip() or "read_public",
        evidence_mode=str(evidence_mode or "candidate_only").strip() or "candidate_only",
        linked_task_id=str(linked_task_id or "").strip()[:256] or None,
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    topic_result = bridge._post_json(
        str(bridge.config.topic_target_url),
        "/v1/hive/topics",
        request.model_dump(mode="json"),
    )
    topic_id = str(topic_result.get("topic_id") or "")
    return {
        "ok": bool(topic_id),
        "status": "created" if topic_id else "topic_failed",
        "topic_id": topic_id,
        "topic_result": topic_result,
    }


def claim_public_topic(
    bridge: Any,
    *,
    topic_id: str,
    note: str | None = None,
    capability_tags: list[str] | None = None,
    status: str = "active",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled():
        return {"ok": False, "status": "disabled"}
    if not bridge.config.topic_target_url:
        return {"ok": False, "status": "missing_target"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}

    clean_topic_id = str(topic_id or "").strip()
    clean_note = " ".join(str(note or "").split()).strip()[:512] or None
    if not clean_topic_id:
        return {"ok": False, "status": "missing_topic_id"}
    if clean_note and text_privacy_risks(clean_note):
        return {"ok": False, "status": "privacy_blocked_claim"}

    request = HiveTopicClaimRequest(
        topic_id=clean_topic_id,
        agent_id=get_local_peer_id(),
        status=str(status or "active").strip() or "active",
        note=clean_note,
        capability_tags=[str(item).strip()[:64] for item in list(capability_tags or []) if str(item).strip()][:16],
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    claim_result = bridge._post_json(
        str(bridge.config.topic_target_url),
        "/v1/hive/topic-claims",
        request.model_dump(mode="json"),
    )
    return {
        "ok": bool(claim_result.get("claim_id")),
        "status": "claimed" if claim_result.get("claim_id") else "claim_failed",
        "claim_id": str(claim_result.get("claim_id") or ""),
        "topic_id": clean_topic_id,
        "claim_result": claim_result,
    }


def post_topic_update(
    bridge: Any,
    *,
    topic_id: str,
    body: str,
    post_kind: str,
    stance: str,
    evidence_refs: list[dict[str, Any]] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    post = HivePostCreateRequest(
        topic_id=str(topic_id or "").strip(),
        author_agent_id=get_local_peer_id(),
        post_kind=str(post_kind or "analysis").strip() or "analysis",
        stance=str(stance or "support").strip() or "support",
        body=str(body or "").strip(),
        evidence_refs=[dict(item) for item in list(evidence_refs or []) if isinstance(item, dict)],
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    return bridge._post_json(
        str(bridge.config.topic_target_url),
        "/v1/hive/posts",
        post.model_dump(mode="json"),
    )


def update_topic_status(
    bridge: Any,
    *,
    topic_id: str,
    status: str,
    note: str | None = None,
    claim_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    request = HiveTopicStatusUpdateRequest(
        topic_id=str(topic_id or "").strip(),
        updated_by_agent_id=get_local_peer_id(),
        status=str(status or "researching").strip() or "researching",
        note=" ".join(str(note or "").split()).strip()[:512] or None,
        claim_id=str(claim_id or "").strip() or None,
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    return bridge._post_json(
        str(bridge.config.topic_target_url),
        "/v1/hive/topic-status",
        request.model_dump(mode="json"),
    )


def topic_result_settlement_helpers(
    bridge: Any,
    *,
    topic_id: str,
    claim_id: str,
) -> list[str]:
    claim_rows = list_public_topic_claims(bridge, topic_id, limit=200)
    clean_claim_id = str(claim_id or "").strip()
    if clean_claim_id:
        for row in claim_rows:
            if str(row.get("claim_id") or "").strip() != clean_claim_id:
                continue
            agent_id = str(row.get("agent_id") or "").strip()
            if agent_id:
                return [agent_id]
    helper_peer_ids: list[str] = []
    seen_helpers: set[str] = set()
    for row in claim_rows:
        claim_status = str(row.get("status") or "").strip().lower()
        if claim_status not in {"active", "completed"}:
            continue
        agent_id = str(row.get("agent_id") or "").strip()
        if not agent_id or agent_id in seen_helpers:
            continue
        seen_helpers.add(agent_id)
        helper_peer_ids.append(agent_id)
    return helper_peer_ids
