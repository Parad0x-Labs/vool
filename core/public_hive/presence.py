from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.meet_and_greet_models import PresenceUpsertRequest
from core.public_hive.truth import normalize_presence_status
from network.signer import get_local_peer_id


def sync_presence(
    bridge: Any,
    *,
    agent_name: str,
    capabilities: list[str],
    status: str = "idle",
    transport_mode: str = "vool_agent",
) -> dict[str, Any]:
    if not bridge.enabled():
        return {"ok": False, "status": "disabled", "posted_to": [], "errors": []}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth", "posted_to": [], "errors": ["public hive auth is not configured"]}

    request = build_presence_request(
        bridge,
        agent_name=agent_name,
        capabilities=capabilities,
        status=status,
        transport_mode=transport_mode,
    )
    # This public door IS the owning entry point of the presence fetches (a caller may be a
    # test, an embedder or the agent's start handshake): the named background scope lets the
    # transport's own fences -- pytest's live-network block included -- decide, instead of the
    # effect gateway denying the fetch as outside any turn before they ever run.
    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("public_hive.presence.sync"):
        return bridge._post_many(
            "/v1/presence/register",
            payload=request.model_dump(mode="json", exclude_defaults=True, exclude_none=True),
            base_urls=bridge.config.meet_seed_urls,
        )


def heartbeat_presence(
    bridge: Any,
    *,
    agent_name: str,
    capabilities: list[str],
    status: str = "idle",
    transport_mode: str = "vool_agent",
) -> dict[str, Any]:
    if not bridge.enabled():
        return {"ok": False, "status": "disabled", "posted_to": [], "errors": []}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth", "posted_to": [], "errors": ["public hive auth is not configured"]}

    request = build_presence_request(
        bridge,
        agent_name=agent_name,
        capabilities=capabilities,
        status=status,
        transport_mode=transport_mode,
    )
    return bridge._post_many(
        "/v1/presence/heartbeat",
        payload=request.model_dump(mode="json", exclude_defaults=True, exclude_none=True),
        base_urls=bridge.config.meet_seed_urls,
    )


def build_presence_request(
    bridge: Any,
    *,
    agent_name: str,
    capabilities: list[str],
    status: str,
    transport_mode: str,
) -> PresenceUpsertRequest:
    return PresenceUpsertRequest(
        agent_id=get_local_peer_id(),
        agent_name=str(agent_name or "").strip()[:64] or None,
        status=normalize_presence_status(status),
        capabilities=[str(item).strip()[:64] for item in capabilities if str(item).strip()][:32],
        home_region=str(bridge.config.home_region or "global")[:64] or "global",
        current_region=str(bridge.config.home_region or "global")[:64] or "global",
        transport_mode=str(transport_mode or "vool_agent")[:64] or "vool_agent",
        trust_score=0.5,
        timestamp=datetime.now(timezone.utc),
        lease_seconds=300,
    )
