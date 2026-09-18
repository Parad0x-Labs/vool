from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.knowledge_freshness import lease_expiry
from storage.knowledge_index import upsert_presence_lease


@dataclass
class PresenceRouteResult:
    ok: bool
    reason: str


def handle_presence_message(msg_type: str, payload_model: Any) -> PresenceRouteResult:
    if msg_type not in {"HELLO_AD", "PRESENCE_HEARTBEAT"}:
        return PresenceRouteResult(False, f"Unsupported presence msg_type: {msg_type}")

    upsert_presence_lease(
        peer_id=payload_model.agent_id,
        agent_name=getattr(payload_model, "agent_name", None),
        status=payload_model.status,
        capabilities=list(payload_model.capabilities),
        home_region=str(getattr(payload_model, "home_region", None) or "global"),
        current_region=str(getattr(payload_model, "current_region", None) or getattr(payload_model, "home_region", None) or "global"),
        transport_mode=payload_model.transport_mode,
        trust_score=float(payload_model.trust_score),
        lease_expires_at=lease_expiry(int(payload_model.lease_seconds)),
        last_heartbeat_at=payload_model.timestamp.isoformat(),
    )
    return PresenceRouteResult(True, "Presence lease updated.")
